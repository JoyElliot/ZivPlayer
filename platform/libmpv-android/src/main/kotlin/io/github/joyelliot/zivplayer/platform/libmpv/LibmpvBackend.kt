// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.view.Surface
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
import kotlinx.coroutines.withTimeout
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Android-only adapter from the typed ZivPlayer player port to [MpvClient].
 *
 * The player session serializes backend commands. Native callbacks and Surface
 * lifecycle calls may arrive on other threads, so every client call also passes
 * through [nativeGate]. Events enter one lossless channel so seek completion
 * cannot overtake an earlier position sample.
 */
class LibmpvBackend internal constructor(
    context: Context,
    private val clientFactory: MpvClientFactory,
) : PlayerBackend, LibmpvSurfacePort {
    constructor(context: Context) : this(context, BootstrapMpvClient)

    private val applicationContext = context.applicationContext
    private val nativeEvents = Channel<BackendEvent>(capacity = Channel.UNLIMITED)

    override val events: Flow<BackendEvent> = nativeEvents.receiveAsFlow()

    private val lifecycleGate = Mutex()
    private val nativeGate = ReentrantLock(true)
    private val generationFence = LoadGenerationFence()
    private val seekCorrelation = SeekCorrelation()
    private val surfaceController = SurfaceLeaseController<Surface>(
        attachNative = ::attachSurfaceLocked,
        detachNative = ::detachSurfaceLocked,
    )

    @Volatile
    private var fileLoadedGeneration: LoadGeneration? = null

    @Volatile
    private var lastPosition = Milliseconds.ZERO

    @Volatile
    private var lastDuration: Milliseconds? = null

    @Volatile
    private var lastBufferedPosition: Milliseconds? = null

    @Volatile
    private var instance: MpvClient? = null

    @Volatile
    private var closed = false

    @Volatile
    private var nativeUnusable = false

    /** Guarded by [nativeGate]; retained until native teardown succeeds. */
    private var pendingDestroy: MpvClient? = null

    /** Guarded by [lifecycleGate]; paired with [pendingDestroy]. */
    private var pendingObserverRemoval = false

    /** Guarded by [nativeGate]; true once a native Surface attach was attempted. */
    private var surfaceAttachmentAttempted = false

    private val observer = object : MpvClient.Observer {
        override fun onPropertyChanged(change: MpvPropertyChange) {
            val property = change.name
            when (val value = change.value) {
                is MpvPropertyValue.DoubleValue -> onDoubleProperty(property, value.value)
                is MpvPropertyValue.Flag -> onFlagProperty(property, value.value)
                is MpvPropertyValue.Int64,
                is MpvPropertyValue.StringValue,
                is MpvPropertyValue.Unsupported,
                MpvPropertyValue.Unavailable,
                -> Unit
            }
        }

        private fun onDoubleProperty(property: String, value: Double) {
            when (property) {
                PROPERTY_POSITION -> lastPosition = value.toMilliseconds() ?: return
                PROPERTY_DURATION -> lastDuration = value.toMilliseconds()
                PROPERTY_BUFFERED_POSITION -> lastBufferedPosition = value.toMilliseconds()
                else -> return
            }
            emitPosition()
        }

        private fun onFlagProperty(property: String, value: Boolean) {
            val generation = generationFence.activeGeneration() ?: return
            when (property) {
                PROPERTY_PAUSED -> emitActiveSemantic(
                    if (value) {
                        BackendEvent.PlaybackPaused(generation)
                    } else {
                        BackendEvent.PlaybackStarted(generation)
                    },
                )

                PROPERTY_BUFFERING -> emitActiveSemantic(
                    BackendEvent.BufferingChanged(generation, value),
                )
                PROPERTY_SEEKING -> if (value) {
                    armSeekCompletion(generation)
                } else {
                    completeSeek(generation)
                }
            }
        }

        override fun onEvent(event: MpvClientEvent) {
            when (event.type) {
                MpvEventType.START_FILE -> {
                    if (generationFence.onStartFile() != null) {
                        fileLoadedGeneration = null
                    }
                }

                MpvEventType.FILE_LOADED -> {
                    val generation = generationFence.activeGeneration() ?: return
                    fileLoadedGeneration = generation
                    val duration = readPropertyDouble(PROPERTY_DURATION)?.toMilliseconds()
                    lastDuration = duration
                    emitActiveSemantic(
                        BackendEvent.Prepared(
                            generation = generation,
                            duration = duration,
                            capabilities = BOOTSTRAP_CAPABILITIES,
                        ),
                    )
                }

                MpvEventType.PLAYBACK_RESTART -> {
                    val generation = generationFence.activeGeneration() ?: return
                    armSeekCompletion(generation)
                    if (readPropertyBoolean(PROPERTY_PAUSED) == false) {
                        emitActiveSemantic(BackendEvent.PlaybackStarted(generation))
                    }
                }

                MpvEventType.END_FILE -> {
                    val generation = generationFence.onEndFile()
                    val fileWasLoaded = generation != null && fileLoadedGeneration == generation
                    fileLoadedGeneration = null
                    resetSeekCorrelation()
                    if (generation != null) {
                        emitSemantic(endFileEvent(generation, fileWasLoaded, event))
                    }
                }

                MpvEventType.QUEUE_OVERFLOW -> {
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

                else -> Unit
            }
        }
    }

    override suspend fun load(request: BackendLoadRequest) = lifecycleGate.withLock {
        stopActiveFile()
        resetPlaybackState(request.startPosition)

        generationFence.beginLoad(request.generation)
        try {
            // Keep loading deterministic: the core decides whether Prepared
            // should transition into playback and issues play() explicitly.
            withPlayer { player ->
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
            }
        } catch (failure: Throwable) {
            generationFence.cancelLoad(request.generation)
            resetSeekCorrelation()
            runCatching { withExistingPlayer { it.command(arrayOf(COMMAND_STOP)) } }
            throw failure
        }
    }

    override suspend fun play() = lifecycleGate.withLock {
        withPlayer { it.setPropertyBoolean(PROPERTY_PAUSED, false) }
    }

    override suspend fun pause() = lifecycleGate.withLock {
        withPlayer { it.setPropertyBoolean(PROPERTY_PAUSED, true) }
    }

    override suspend fun seekTo(request: BackendSeekRequest) = lifecycleGate.withLock {
        check(request.generation == generationFence.activeGeneration()) {
            "The seek belongs to an inactive load generation."
        }
        seekCorrelation.begin(request)
        try {
            withPlayer { player ->
                player.command(
                    arrayOf(COMMAND_SEEK, request.position.toSecondsString(), SEEK_ABSOLUTE_EXACT),
                )
            }
        } catch (failure: Throwable) {
            seekCorrelation.fail(request)
            throw failure
        }
    }

    override suspend fun stop() = lifecycleGate.withLock {
        stopActiveFile()
        resetPlaybackState(Milliseconds.ZERO)
    }

    override suspend fun setVolume(volume: VolumePercent) = lifecycleGate.withLock {
        withPlayer { it.setPropertyDouble(PROPERTY_VOLUME, volume.value.toDouble()) }
    }

    override suspend fun setPlaybackRate(
        playbackRate: PlaybackRatePermille,
    ) = lifecycleGate.withLock {
        withPlayer {
            it.setPropertyDouble(
                PROPERTY_SPEED,
                playbackRate.value.toDouble() / PERMILLE_DIVISOR,
            )
        }
    }

    override suspend fun setMuted(muted: Boolean) = lifecycleGate.withLock {
        withPlayer { it.setPropertyBoolean(PROPERTY_MUTE, muted) }
    }

    override suspend fun selectTrack(
        kind: TrackKind,
        trackId: TrackId?,
    ) = lifecycleGate.withLock {
        val property = when (kind) {
            TrackKind.AUDIO -> PROPERTY_AUDIO_TRACK
            TrackKind.VIDEO -> PROPERTY_VIDEO_TRACK
            TrackKind.SUBTITLE -> PROPERTY_SUBTITLE_TRACK
        }
        val selection = trackId?.value ?: TRACK_DISABLED
        withPlayer { it.setPropertyString(property, selection) }
    }

    override fun attachSurface(surface: Surface): LibmpvSurfaceLease = nativeGate.withLock {
        check(!closed) { "The libmpv backend is closed." }
        check(!nativeUnusable) { "The libmpv backend requires recreation." }
        surfaceController.attach(surface)
    }

    override fun detachSurface(lease: LibmpvSurfaceLease) = nativeGate.withLock {
        if (!closed) {
            surfaceController.detach(lease)
        }
    }

    override suspend fun close() = lifecycleGate.withLock closeLock@{
        var playerToDestroy: MpvClient? = null
        var surfaceFailure: Throwable? = null
        var firstClose = false

        nativeGate.withLock {
            if (closed && pendingDestroy == null) {
                return@closeLock
            }

            if (!closed) {
                firstClose = true
                closed = true
                pendingDestroy = instance
                pendingObserverRemoval = instance != null
                instance = null
            }

            runCatching { surfaceController.close() }
                .exceptionOrNull()
                ?.let { surfaceFailure = it }
            playerToDestroy = pendingDestroy
        }

        if (firstClose) {
            generationFence.close()
            resetSeekCorrelation()
            resetPlaybackState(Milliseconds.ZERO)
        }

        // A client may synchronize observer removal with its event pump, and
        // destroy() may join that thread. Quiesce under nativeGate, then run
        // both operations outside it so callbacks can observe `closed` and
        // leave without a callback/nativeGate or join/nativeGate cycle.
        var teardownFailure: Throwable? = surfaceFailure
        playerToDestroy?.let { player ->
            val observerFailure = if (pendingObserverRemoval) {
                runCatching { player.removeObserver(observer) }
                    .onSuccess { pendingObserverRemoval = false }
                    .exceptionOrNull()
            } else {
                null
            }
            observerFailure?.let {
                teardownFailure = combineFailures(teardownFailure, it)
            }
            val destroyFailure = runCatching { player.destroy() }.exceptionOrNull()
            if (destroyFailure == null) {
                nativeGate.withLock {
                    if (pendingDestroy === player) {
                        surfaceController.completeAfterNativeDestroy()
                        pendingDestroy = null
                        pendingObserverRemoval = false
                        surfaceAttachmentAttempted = false
                    }
                }
            } else {
                teardownFailure = combineFailures(teardownFailure, destroyFailure)
            }
        }

        if (firstClose) {
            nativeEvents.close()
        }
        teardownFailure?.let { throw it }
        Unit
    }

    private inline fun <Result> withPlayer(block: (MpvClient) -> Result): Result =
        nativeGate.withLock {
            check(!closed) { "The libmpv backend is closed." }
            check(!nativeUnusable) { "The libmpv backend requires recreation." }
            block(requireInstanceLocked())
        }

    private inline fun <Result> withExistingPlayer(block: (MpvClient) -> Result): Result? =
        nativeGate.withLock {
            if (closed || nativeUnusable) {
                null
            } else {
                instance?.let(block)
            }
        }

    private fun requireInstanceLocked(): MpvClient {
        check(!closed) { "The libmpv backend is closed." }
        instance?.let { return it }

        val player = checkNotNull(clientFactory.create(applicationContext)) {
            "The libmpv instance could not be created."
        }
        try {
            player.addObserver(observer)
            player.setOptionString(OPTION_CONFIG, OPTION_DISABLED)
            player.setOptionString(OPTION_FORCE_WINDOW, OPTION_ENABLED)
            player.initialize()
            player.observeProperty(
                PROPERTY_POSITION,
                MpvPropertyFormat.DOUBLE,
                OBSERVER_POSITION,
            )
            player.observeProperty(
                PROPERTY_DURATION,
                MpvPropertyFormat.DOUBLE,
                OBSERVER_DURATION,
            )
            player.observeProperty(
                PROPERTY_BUFFERED_POSITION,
                MpvPropertyFormat.DOUBLE,
                OBSERVER_BUFFERED_POSITION,
            )
            player.observeProperty(
                PROPERTY_PAUSED,
                MpvPropertyFormat.FLAG,
                OBSERVER_PAUSED,
            )
            player.observeProperty(
                PROPERTY_BUFFERING,
                MpvPropertyFormat.FLAG,
                OBSERVER_BUFFERING,
            )
            player.observeProperty(
                PROPERTY_SEEKING,
                MpvPropertyFormat.FLAG,
                OBSERVER_SEEKING,
            )
        } catch (failure: Throwable) {
            // Cleanup cannot safely run while nativeGate is held: a client may
            // synchronize observer callbacks and join its event pump during
            // destroy. Retain the partial instance as unusable; close()
            // performs two-phase cleanup after the caller receives RESET.
            instance = player
            nativeUnusable = true
            throw failure
        }
        instance = player
        return player
    }

    private suspend fun stopActiveFile() {
        val stopped = generationFence.beginStop() ?: return
        try {
            withPlayer { it.command(arrayOf(COMMAND_STOP)) }
            withTimeout(NATIVE_STOP_TIMEOUT_MILLIS) {
                stopped.await()
            }
        } finally {
            if (!stopped.isCompleted) {
                generationFence.cancelStop(stopped)
            }
        }
    }

    private fun completeSeek(generation: LoadGeneration) {
        val request = seekCorrelation.complete(generation) ?: return

        val position = readPropertyDouble(PROPERTY_POSITION)?.toMilliseconds() ?: request.position
        lastPosition = position
        emitActiveSemantic(
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
        if (closed) {
            return
        }
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
        if (!closed) {
            nativeEvents.trySend(event)
        }
    }

    private fun emitActiveSemantic(event: BackendEvent) {
        if (!closed && generationFence.activeGeneration() == event.generation) {
            nativeEvents.trySend(event)
        }
    }

    private fun endFileEvent(
        generation: LoadGeneration,
        fileWasLoaded: Boolean,
        event: MpvClientEvent,
    ): BackendEvent = when (event.endReason) {
        MpvEndFileReason.ERROR -> BackendEvent.Failure(
            generation = generation,
            error = PlayerError(
                kind = PlayerErrorKind.SOURCE_UNAVAILABLE,
                message = "libmpv could not play the media (error ${event.endErrorCode}).",
                recovery = ErrorRecovery.RETRY,
            ),
        )

        MpvEndFileReason.QUIT,
        MpvEndFileReason.REDIRECT,
        MpvEndFileReason.UNKNOWN,
        -> BackendEvent.Failure(
            generation = generation,
            error = PlayerError(
                kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
                message = "libmpv ended playback for an unsupported reason.",
                recovery = ErrorRecovery.RESET,
            ),
        )

        MpvEndFileReason.EOF,
        MpvEndFileReason.STOP,
        null,
        -> if (fileWasLoaded) {
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
        }
    }

    private fun readPropertyDouble(property: String): Double? = nativeGate.withLock {
        if (closed || nativeUnusable) null else instance?.getPropertyDouble(property)
    }

    private fun readPropertyBoolean(property: String): Boolean? = nativeGate.withLock {
        if (closed || nativeUnusable) null else instance?.getPropertyBoolean(property)
    }

    private fun attachSurfaceLocked(surface: Surface) {
        val player = requireInstanceLocked()
        surfaceAttachmentAttempted = true
        player.attachSurface(surface)
    }

    private fun detachSurfaceLocked() {
        if (!surfaceAttachmentAttempted) {
            return
        }
        val player = instance ?: pendingDestroy ?: return
        player.detachSurface()
        surfaceAttachmentAttempted = false
    }

    private fun Double.toMilliseconds(): Milliseconds? {
        if (!isFinite() || this < 0.0 || this > Long.MAX_VALUE / MILLIS_PER_SECOND) {
            return null
        }
        return Milliseconds((this * MILLIS_PER_SECOND).toLong())
    }

    private fun Milliseconds.toSecondsString(): String =
        (value.toDouble() / MILLIS_PER_SECOND).toString()

    private fun Throwable.addSuppressedDistinct(failure: Throwable) {
        if (failure !== this) {
            addSuppressed(failure)
        }
    }

    private fun combineFailures(primary: Throwable?, additional: Throwable): Throwable =
        primary?.also { it.addSuppressedDistinct(additional) } ?: additional

    private companion object {
        const val MILLIS_PER_SECOND = 1_000.0
        const val PERMILLE_DIVISOR = 1_000.0
        const val NATIVE_STOP_TIMEOUT_MILLIS = 5_000L

        const val OBSERVER_POSITION = 1L
        const val OBSERVER_DURATION = 2L
        const val OBSERVER_BUFFERED_POSITION = 3L
        const val OBSERVER_PAUSED = 4L
        const val OBSERVER_BUFFERING = 5L
        const val OBSERVER_SEEKING = 6L

        const val COMMAND_LOAD_FILE = "loadfile"
        const val COMMAND_SEEK = "seek"
        const val COMMAND_STOP = "stop"
        const val LOAD_REPLACE = "replace"
        const val NO_PLAYLIST_INDEX = "-1"
        const val SEEK_ABSOLUTE_EXACT = "absolute+exact"
        const val OPTION_START = "start"
        const val OPTION_CONFIG = "config"
        const val OPTION_FORCE_WINDOW = "force-window"
        const val OPTION_DISABLED = "no"
        const val OPTION_ENABLED = "yes"
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
