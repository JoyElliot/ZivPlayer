// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import dev.jdtech.mpv.MPVLib
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.VolumePercent
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.PlayerCapabilities
import io.github.joyelliot.zivplayer.core.player.PlayerCapability
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.PlayerErrorKind
import io.github.joyelliot.zivplayer.core.player.runtime.BackendEvent
import io.github.joyelliot.zivplayer.core.player.runtime.BackendLoadRequest
import io.github.joyelliot.zivplayer.core.player.runtime.BackendSeekRequest
import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import io.github.joyelliot.zivplayer.core.player.runtime.PlayerBackend
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Android-only bootstrap adapter for the reviewed libmpv AAR.
 *
 * The player session serializes every public method call. Native callbacks are
 * delivered on libmpv's event thread and enter one lossless channel so seek
 * completion cannot overtake an earlier position sample.
 */
class LibmpvBackend(context: Context) : PlayerBackend {
    private val applicationContext = context.applicationContext
    private val nativeEvents = Channel<BackendEvent>(capacity = Channel.UNLIMITED)

    override val events: Flow<BackendEvent> = nativeEvents.receiveAsFlow()

    private val lifecycleGate = Mutex()
    private val generationFence = LoadGenerationFence()
    private val seekCorrelation = SeekCorrelation()

    @Volatile
    private var fileLoadedGeneration: LoadGeneration? = null

    @Volatile
    private var lastPosition = Milliseconds.ZERO

    @Volatile
    private var lastDuration: Milliseconds? = null

    @Volatile
    private var lastBufferedPosition: Milliseconds? = null

    @Volatile
    private var instance: MPVLib? = null

    @Volatile
    private var closed = false

    private val observer = object : MPVLib.EventObserver {
        override fun eventProperty(property: String) = Unit

        override fun eventProperty(property: String, value: Long) = Unit

        override fun eventProperty(property: String, value: Double) {
            when (property) {
                PROPERTY_POSITION -> lastPosition = value.toMilliseconds() ?: return
                PROPERTY_DURATION -> lastDuration = value.toMilliseconds()
                PROPERTY_BUFFERED_POSITION -> lastBufferedPosition = value.toMilliseconds()
                else -> return
            }
            emitPosition()
        }

        override fun eventProperty(property: String, value: Boolean) {
            val generation = generationFence.activeGeneration() ?: return
            when (property) {
                PROPERTY_PAUSED -> emitSemantic(
                    if (value) {
                        BackendEvent.PlaybackPaused(generation)
                    } else {
                        BackendEvent.PlaybackStarted(generation)
                    },
                )

                PROPERTY_BUFFERING -> emitSemantic(BackendEvent.BufferingChanged(generation, value))
                PROPERTY_SEEKING -> if (value) {
                    armSeekCompletion(generation)
                } else {
                    completeSeek(generation)
                }
            }
        }

        override fun eventProperty(property: String, value: String) = Unit

        override fun event(eventId: Int) {
            when (eventId) {
                MPVLib.MpvEvent.MPV_EVENT_START_FILE -> {
                    if (generationFence.onStartFile() != null) {
                        fileLoadedGeneration = null
                    }
                }
                MPVLib.MpvEvent.MPV_EVENT_FILE_LOADED -> {
                    val generation = generationFence.activeGeneration() ?: return
                    fileLoadedGeneration = generation
                    val duration = instance?.getPropertyDouble(PROPERTY_DURATION)?.toMilliseconds()
                    lastDuration = duration
                    emitSemantic(
                        BackendEvent.Prepared(
                            generation = generation,
                            duration = duration,
                            capabilities = BOOTSTRAP_CAPABILITIES,
                        ),
                    )
                }

                MPVLib.MpvEvent.MPV_EVENT_PLAYBACK_RESTART -> {
                    val generation = generationFence.activeGeneration() ?: return
                    armSeekCompletion(generation)
                    if (instance?.getPropertyBoolean(PROPERTY_PAUSED) == false) {
                        emitSemantic(BackendEvent.PlaybackStarted(generation))
                    }
                }

                MPVLib.MpvEvent.MPV_EVENT_END_FILE -> {
                    val generation = generationFence.onEndFile()
                    val fileWasLoaded = generation != null && fileLoadedGeneration == generation
                    fileLoadedGeneration = null
                    resetSeekCorrelation()
                    if (generation != null) {
                        emitSemantic(
                            if (fileWasLoaded) {
                                BackendEvent.Ended(generation)
                            } else {
                                BackendEvent.Failure(
                                    generation = generation,
                                    error = PlayerError(
                                        kind = PlayerErrorKind.SOURCE_UNAVAILABLE,
                                        message = "libmpv ended before the media was prepared.",
                                        recovery = ErrorRecovery.RETRY,
                                    ),
                                )
                            },
                        )
                    }
                }

                MPVLib.MpvEvent.MPV_EVENT_QUEUE_OVERFLOW -> {
                    val generation = generationFence.failCurrent() ?: return
                    fileLoadedGeneration = null
                    resetSeekCorrelation()
                    emitSemantic(
                        BackendEvent.Failure(
                            generation = generation,
                            error = PlayerError(
                                kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
                                message = "The playback event queue overflowed.",
                                recovery = ErrorRecovery.RESET,
                            ),
                        ),
                    )
                }
            }
        }
    }

    override suspend fun load(request: BackendLoadRequest) = lifecycleGate.withLock {
        val player = requireInstance()
        stopActiveFile(player)
        resetPlaybackState(request.startPosition)

        generationFence.beginLoad(request.generation)
        try {
            // Keep loading deterministic: the core decides whether Prepared
            // should transition into playback and issues play() explicitly.
            player.setPropertyBoolean(PROPERTY_PAUSED, true)
            player.command(
                arrayOf(
                    COMMAND_LOAD_FILE,
                    request.item.media.source.locator,
                    LOAD_REPLACE,
                    NO_PLAYLIST_INDEX,
                    "$OPTION_START=${request.startPosition.toSecondsString()}",
                ),
            )
        } catch (failure: Throwable) {
            generationFence.cancelLoad(request.generation)
            resetSeekCorrelation()
            runCatching { player.command(arrayOf(COMMAND_STOP)) }
            throw failure
        }
    }

    override suspend fun play() {
        requireInstance().setPropertyBoolean(PROPERTY_PAUSED, false)
    }

    override suspend fun pause() {
        requireInstance().setPropertyBoolean(PROPERTY_PAUSED, true)
    }

    override suspend fun seekTo(request: BackendSeekRequest) {
        check(request.generation == generationFence.activeGeneration()) {
            "The seek belongs to an inactive load generation."
        }
        seekCorrelation.begin(request)
        try {
            requireInstance().command(
                arrayOf(COMMAND_SEEK, request.position.toSecondsString(), SEEK_ABSOLUTE_EXACT),
            )
        } catch (failure: Throwable) {
            seekCorrelation.fail(request)
            throw failure
        }
    }

    override suspend fun stop() = lifecycleGate.withLock {
        stopActiveFile(requireInstance())
        resetPlaybackState(Milliseconds.ZERO)
    }

    override suspend fun setVolume(volume: VolumePercent) {
        requireInstance().setPropertyDouble(PROPERTY_VOLUME, volume.value.toDouble())
    }

    override suspend fun setPlaybackRate(playbackRate: PlaybackRatePermille) {
        requireInstance().setPropertyDouble(
            PROPERTY_SPEED,
            playbackRate.value.toDouble() / PERMILLE_DIVISOR,
        )
    }

    override suspend fun setMuted(muted: Boolean) {
        requireInstance().setPropertyBoolean(PROPERTY_MUTE, muted)
    }

    override suspend fun selectTrack(kind: TrackKind, trackId: TrackId?) {
        val property = when (kind) {
            TrackKind.AUDIO -> PROPERTY_AUDIO_TRACK
            TrackKind.VIDEO -> PROPERTY_VIDEO_TRACK
            TrackKind.SUBTITLE -> PROPERTY_SUBTITLE_TRACK
        }
        val selection = trackId?.value ?: TRACK_DISABLED
        requireInstance().setPropertyString(property, selection)
    }

    override suspend fun close() = lifecycleGate.withLock {
        if (closed) {
            return@withLock
        }
        closed = true
        generationFence.close()
        resetSeekCorrelation()
        resetPlaybackState(Milliseconds.ZERO)
        val player = instance
        instance = null

        var closeFailure: Throwable? = null
        if (player != null) {
            try {
                player.removeObserver(observer)
            } catch (failure: Throwable) {
                closeFailure = failure
            }
            try {
                player.destroy()
            } catch (failure: Throwable) {
                closeFailure?.addSuppressed(failure) ?: run { closeFailure = failure }
            }
        }
        nativeEvents.close()
        closeFailure?.let { throw it }
        Unit
    }

    private fun requireInstance(): MPVLib {
        check(!closed) { "The libmpv backend is closed." }
        instance?.let { return it }

        val player = checkNotNull(MPVLib.create(applicationContext)) {
            "The libmpv instance could not be created."
        }
        try {
            player.addObserver(observer)
            check(player.setOptionString(OPTION_CONFIG, OPTION_DISABLED) >= 0) {
                "The libmpv configuration policy could not be applied."
            }
            player.init()
            player.observeProperty(PROPERTY_POSITION, MPVLib.MpvFormat.MPV_FORMAT_DOUBLE)
            player.observeProperty(PROPERTY_DURATION, MPVLib.MpvFormat.MPV_FORMAT_DOUBLE)
            player.observeProperty(PROPERTY_BUFFERED_POSITION, MPVLib.MpvFormat.MPV_FORMAT_DOUBLE)
            player.observeProperty(PROPERTY_PAUSED, MPVLib.MpvFormat.MPV_FORMAT_FLAG)
            player.observeProperty(PROPERTY_BUFFERING, MPVLib.MpvFormat.MPV_FORMAT_FLAG)
            player.observeProperty(PROPERTY_SEEKING, MPVLib.MpvFormat.MPV_FORMAT_FLAG)
        } catch (failure: Throwable) {
            runCatching { player.removeObserver(observer) }
                .exceptionOrNull()
                ?.let(failure::addSuppressed)
            runCatching { player.destroy() }
                .exceptionOrNull()
                ?.let(failure::addSuppressed)
            throw failure
        }
        instance = player
        return player
    }

    private suspend fun stopActiveFile(player: MPVLib) {
        val stopped = generationFence.beginStop() ?: return
        try {
            player.command(arrayOf(COMMAND_STOP))
            stopped.await()
        } finally {
            if (!stopped.isCompleted) {
                generationFence.cancelStop(stopped)
            }
        }
    }

    private fun completeSeek(generation: LoadGeneration) {
        val request = seekCorrelation.complete(generation) ?: return

        val position = instance?.getPropertyDouble(PROPERTY_POSITION)?.toMilliseconds() ?: request.position
        lastPosition = position
        emitSemantic(
            BackendEvent.SeekCompleted(
                generation = generation,
                seekGeneration = request.seekGeneration,
                position = position,
            ),
        )
    }

    private fun armSeekCompletion(generation: LoadGeneration) {
        seekCorrelation.armCompletion(generation)
    }

    private fun resetSeekCorrelation() {
        seekCorrelation.reset()
    }

    private fun resetPlaybackState(position: Milliseconds) {
        fileLoadedGeneration = null
        lastPosition = position
        lastDuration = null
        lastBufferedPosition = null
        resetSeekCorrelation()
    }

    private fun emitPosition() {
        val generation = generationFence.activeGeneration() ?: return
        val seekGeneration = seekCorrelation.positionEpoch(generation)
        nativeEvents.trySend(
            BackendEvent.PositionChanged(
                generation = generation,
                position = lastPosition,
                duration = lastDuration,
                bufferedPosition = lastBufferedPosition,
                seekGeneration = seekGeneration,
            ),
        )
    }

    private fun emitSemantic(event: BackendEvent) {
        nativeEvents.trySend(event)
    }

    private fun Double.toMilliseconds(): Milliseconds? {
        if (!isFinite() || this < 0.0 || this > Long.MAX_VALUE / MILLIS_PER_SECOND) {
            return null
        }
        return Milliseconds((this * MILLIS_PER_SECOND).toLong())
    }

    private fun Milliseconds.toSecondsString(): String =
        (value.toDouble() / MILLIS_PER_SECOND).toString()

    private companion object {
        const val MILLIS_PER_SECOND = 1_000.0
        const val PERMILLE_DIVISOR = 1_000.0

        const val COMMAND_LOAD_FILE = "loadfile"
        const val COMMAND_SEEK = "seek"
        const val COMMAND_STOP = "stop"
        const val LOAD_REPLACE = "replace"
        const val NO_PLAYLIST_INDEX = "-1"
        const val SEEK_ABSOLUTE_EXACT = "absolute+exact"
        const val OPTION_START = "start"
        const val OPTION_CONFIG = "config"
        const val OPTION_DISABLED = "no"
        const val TRACK_DISABLED = "no"

        const val PROPERTY_POSITION = "time-pos"
        const val PROPERTY_DURATION = "duration"
        const val PROPERTY_BUFFERED_POSITION = "demuxer-cache-time"
        const val PROPERTY_PAUSED = "pause"
        const val PROPERTY_BUFFERING = "paused-for-cache"
        const val PROPERTY_SEEKING = "seeking"
        const val PROPERTY_VOLUME = "volume"
        const val PROPERTY_SPEED = "speed"
        const val PROPERTY_MUTE = "mute"
        const val PROPERTY_AUDIO_TRACK = "aid"
        const val PROPERTY_VIDEO_TRACK = "vid"
        const val PROPERTY_SUBTITLE_TRACK = "sid"

        val BOOTSTRAP_CAPABILITIES = PlayerCapabilities.of(PlayerCapability.entries.toSet())
    }
}
