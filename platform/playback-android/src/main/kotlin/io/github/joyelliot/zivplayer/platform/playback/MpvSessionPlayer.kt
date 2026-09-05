// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(
    markerClass = [androidx.media3.common.util.UnstableApi::class],
)

package io.github.joyelliot.zivplayer.platform.playback

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import android.util.Log
import android.view.Surface
import androidx.core.net.toUri
import androidx.media3.common.C
import androidx.media3.common.AudioAttributes
import androidx.media3.common.MediaItem as Media3MediaItem
import androidx.media3.common.MediaMetadata as Media3MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.common.SimpleBasePlayer
import androidx.media3.common.TrackSelectionParameters
import androidx.media3.common.Tracks
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.SettableFuture
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaItem
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import io.github.joyelliot.zivplayer.core.model.MediaSource
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.model.SubtitleSource
import io.github.joyelliot.zivplayer.core.model.VolumePercent
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.media.MediaPlaybackPreferences
import io.github.joyelliot.zivplayer.core.media.SavedSubtitle
import io.github.joyelliot.zivplayer.core.media.matchTrack
import io.github.joyelliot.zivplayer.core.media.savedSelection
import io.github.joyelliot.zivplayer.core.player.CommandResult
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.PlaybackSnapshot
import io.github.joyelliot.zivplayer.core.player.PlayerCapability
import io.github.joyelliot.zivplayer.core.player.PlayerCommand
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.PlayerSession
import io.github.joyelliot.zivplayer.core.player.PlayerStatus
import io.github.joyelliot.zivplayer.core.player.RepeatMode
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvSurfaceLease
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvSurfacePort
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvConfigurationPort
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvResourcePaths
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.android.asCoroutineDispatcher
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.UUID
import kotlin.math.roundToInt

private const val MILLIS_TO_MICROS = 1_000L

internal data class PlaybackEngine(
    val session: PlayerSession,
    val surfacePort: LibmpvSurfacePort,
    val configurationPort: LibmpvConfigurationPort? = null,
)

internal class QueueItemIdGenerator(
    private val instanceId: String = UUID.randomUUID().toString(),
) {
    private val sequence = AtomicLong(0L)

    fun next(): QueueItemId = QueueItemId("queue:$instanceId:${sequence.getAndIncrement()}")
}

internal class SetMediaRequestFence(
    private val nextSequence: () -> Long = PlaybackRequestSequencer::next,
    private val currentSequence: () -> Long = PlaybackRequestSequencer::current,
    private val invalidateSequence: () -> Unit = { PlaybackRequestSequencer.invalidate() },
) {
    private val generation = AtomicLong(0L)
    private val pendingGeneration = AtomicLong(NO_PENDING_GENERATION)
    private val pendingSequence = AtomicLong(NO_PENDING_SEQUENCE)

    fun begin(requestSequence: Long?): Ticket {
        val sequence = requestSequence ?: nextSequence()
        if (sequence != currentSequence()) return Ticket(sequence, STALE_GENERATION)
        val ticket = Ticket(sequence, generation.incrementAndGet())
        pendingSequence.set(ticket.sequence)
        pendingGeneration.set(ticket.generation)
        return ticket
    }

    fun invalidate() {
        invalidateSequence()
        generation.incrementAndGet()
        pendingGeneration.set(NO_PENDING_GENERATION)
        pendingSequence.set(NO_PENDING_SEQUENCE)
    }

    fun isCurrent(ticket: Ticket): Boolean =
        ticket.generation == generation.get() && ticket.sequence == currentSequence()

    fun hasPendingRequest(): Boolean =
        pendingGeneration.get() == generation.get() && pendingSequence.get() == currentSequence()

    fun finish(ticket: Ticket) {
        if (pendingGeneration.compareAndSet(ticket.generation, NO_PENDING_GENERATION)) {
            pendingSequence.compareAndSet(ticket.sequence, NO_PENDING_SEQUENCE)
        }
    }

    data class Ticket(
        val sequence: Long,
        val generation: Long,
    )

    private companion object {
        const val STALE_GENERATION = Long.MIN_VALUE
        const val NO_PENDING_GENERATION = Long.MIN_VALUE
        const val NO_PENDING_SEQUENCE = Long.MIN_VALUE
    }
}

internal class MpvSessionPlayer(
    applicationLooper: Looper,
    private val context: Context,
    private val queueItemIdGenerator: QueueItemIdGenerator = QueueItemIdGenerator(),
    private val setMediaRequestFence: SetMediaRequestFence = SetMediaRequestFence(),
    private val progressRecorder: PlaybackProgressRecorder,
    private val ensurePlaybackForeground: () -> Unit,
    private val cancelPendingForeground: () -> Unit,
    private val onPlaybackPublished: () -> Unit,
    private val preferencesProvider: PlaybackHistoryProvider? = null,
    private val onConfigurationStatus: (String?) -> Unit = {},
    private val engineFactory: () -> PlaybackEngine,
) : SimpleBasePlayer(applicationLooper) {
    private val applicationHandler = Handler(applicationLooper)
    private val scope = CoroutineScope(
        SupervisorJob() + applicationHandler.asCoroutineDispatcher("ZivMedia3Player"),
    )
    private val shutdownStarted = AtomicBoolean(false)
    private val shutdownCompletion = CompletableDeferred<Unit>()
    private val operationGate = Mutex()
    private val audioFocusPolicy = PlaybackAudioFocusPolicy()
    private val audioFocus = AndroidPlaybackAudioFocus(context, applicationLooper, ::onAudioFocusChanged)
    private var playWhenReadyChangeReason = Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST

    private var engine = engineFactory()
    private var engineEpoch = 0L

    @Volatile
    private var snapshot: PlaybackSnapshot = engine.session.snapshot.value

    private var videoOutput: Any? = null
    private var videoSurfaceRequestToken = 0L
    private var videoSurfaceSize: Pair<Int, Int>? = null
    private var surfaceLease: LibmpvSurfaceLease? = null
    private var activeDescriptor: ParcelFileDescriptor? = null
    private var activeMediaItem: Media3MediaItem? = null
    private val subtitleDescriptors = mutableListOf<ParcelFileDescriptor>()
    private var subtitleRequestGeneration = 0L
    private var snapshotCollector: Job
    private var eventCollector: Job
    private var configurationJob: Job? = null
    private val configurationReady = CompletableDeferred<Unit>()
    private var preferences = PlayerPreferences()
    internal var configurationMessage: String? = null
        private set
    private var pendingDefaultRate: QueueItemId? = null
    private var pendingTrackRestore: PendingTrackRestore? = null
    private var restoringTracks = false
    private var pendingImportedChoice: PendingImportedChoice? = null
    private var trackIntentGeneration = 0L

    init {
        progressRecorder.bind(engineEpoch, snapshot)
        snapshotCollector = startSnapshotCollector(engine, engineEpoch)
        eventCollector = startEventCollector(engine, engineEpoch)
        configurationJob = scope.launch {
            val repository = preferencesProvider?.playerPreferencesRepository
            if (repository == null) { configurationReady.complete(Unit); return@launch }
            try {
                repository.preferences.collect { value ->
                    operationGate.withLock {
                        preferences = value
                        if (!value.rememberTrackSelection) clearPendingTrackRestore()
                        applyPlaybackConfiguration()
                    }
                    configurationReady.complete(Unit)
                }
            } catch (failure: CancellationException) { throw failure
            } catch (failure: Exception) {
                configurationMessage = "播放设置读取失败：${failure.message}"
                onConfigurationStatus(configurationMessage)
            } finally { configurationReady.complete(Unit) }
        }
    }

    override fun getState(): State {
        val snapshot = snapshot
        val projection = snapshot.toMedia3Projection()
        val volume = snapshot.volume.value / PERCENT_DIVISOR
        val builder = State.Builder()
            .setAvailableCommands(snapshot.availableMedia3Commands())
            .setPlayWhenReady(
                snapshot.playWhenReady,
                playWhenReadyChangeReason,
            )
            .setPlaybackState(projection.playbackState)
            .setIsLoading(projection.isLoading)
            .setRepeatMode(snapshot.repeatMode.toMedia3RepeatMode())
            .setPlaybackParameters(
                PlaybackParameters(snapshot.playbackRate.value / PERMILLE_DIVISOR),
            )
            .setVolume(if (snapshot.muted) 0f else volume)
            .setUnmuteVolume(volume)
            .setAudioAttributes(PLAYBACK_AUDIO_ATTRIBUTES)
            .setTrackSelectionParameters(snapshot.queue.currentItem?.id?.value?.let {
                snapshot.tracks.toMedia3SelectionParameters(it)
            } ?: TrackSelectionParameters.DEFAULT)

        if (projection.exposesError) {
            builder.setPlayerError(snapshot.error?.toPlaybackException())
        }

        if (snapshot.queue.items.isNotEmpty()) {
            builder
                .setPlaylist(snapshot.toMedia3Playlist(activeMediaItem))
                .setCurrentMediaItemIndex(checkNotNull(snapshot.queue.currentIndex))
                .setContentPositionMs(snapshot.timeline.position.value)
                .setContentBufferedPositionMs {
                    snapshot.timeline.bufferedPosition?.value ?: snapshot.timeline.position.value
                }
                .setTotalBufferedDurationMs {
                    val buffered = snapshot.timeline.bufferedPosition ?: snapshot.timeline.position
                    (buffered.value - snapshot.timeline.position.value).coerceAtLeast(0L)
                }
        }
        return builder.build()
    }

    override fun handleSetPlayWhenReady(playWhenReady: Boolean): ListenableFuture<*> {
        // The request-owned Play sent after installation must not cancel a newer media
        // request that may already be resolving. Pause remains an explicit cancellation.
        if (!playWhenReady) {
            setMediaRequestFence.invalidate()
            clearPendingTrackRestore()
            releaseAudioFocus()
        }
        if (playWhenReady && setMediaRequestFence.hasPendingRequest()) {
            return Futures.immediateVoidFuture()
        }
        val playSequence = PlaybackRequestSequencer.current()
        val playItem = engine.session.snapshot.value.queue.currentItem?.id
        val restoration = if (playWhenReady) pendingTrackRestore else null
        return launchFuture(beforeOperation = { restoration?.let { awaitInitialTrackRestore(it) } }) {
            if (playWhenReady && (playSequence != PlaybackRequestSequencer.current() ||
                    playItem != engine.session.snapshot.value.queue.currentItem?.id)) return@launchFuture
            playWhenReadyChangeReason = Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST
            if (playWhenReady) {
                try {
                    if (pendingDefaultRate == engine.session.snapshot.value.queue.currentItem?.id) {
                        dispatchOrThrow(PlayerCommand.SetPlaybackRate(preferences.defaultPlaybackRate))
                        pendingDefaultRate = null
                    }
                    check(audioFocusPolicy.requestPlay {
                        ensurePlaybackForeground()
                        audioFocus.request()
                    }) { "Audio focus is unavailable. Playback remains paused." }
                    dispatchOrThrow(PlayerCommand.Play)
                } catch (failure: Throwable) {
                    releaseAudioFocus()
                    throw failure
                }
            } else {
                dispatchOrThrow(PlayerCommand.Pause)
            }
            if (!playWhenReady) {
                progressRecorder.flushPause(engineEpoch, engine.session.snapshot.value)
            }
        }
    }

    override fun handlePrepare(): ListenableFuture<*> = Futures.immediateVoidFuture()

    override fun handleStop(): ListenableFuture<*> {
        setMediaRequestFence.invalidate()
        subtitleRequestGeneration++
        ++trackIntentGeneration
        clearPendingTrackRestore()
        pendingImportedChoice = null
        releaseAudioFocus()
        return launchFuture {
            val beforeStop = engine.session.snapshot.value
            dispatchOrThrow(PlayerCommand.Stop)
            if (beforeStop.queue.currentItem != null && beforeStop.status != PlayerStatus.IDLE) {
                progressRecorder.flushStop(engineEpoch, engine.session.snapshot.value)
            }
        }
    }

    override fun handleRelease(): ListenableFuture<*> = shutdownAsync()

    override fun handleSetRepeatMode(repeatMode: Int): ListenableFuture<*> = dispatch(
        PlayerCommand.SetRepeatMode(repeatMode.toCoreRepeatMode()),
    )

    override fun handleSetTrackSelectionParameters(parameters: TrackSelectionParameters): ListenableFuture<*> {
        ++trackIntentGeneration
        clearPendingTrackRestore() // An explicit choice always wins over a delayed restore.
        pendingImportedChoice = null
        return launchFuture {
        clearPendingTrackRestore()
        pendingImportedChoice = null
        val current = engine.session.snapshot.value
        val queueItem = checkNotNull(current.queue.currentItem) { "No media is loaded." }
        val commands = current.tracks.selectionCommands(queueItem.id.value, parameters)
        commands.forEach { command ->
            dispatchOrThrow(command)
            persistChoice {
                preferencesProvider?.mediaPlaybackPreferencesRepository?.saveTrack(queueItem.media.id,
                    command.trackId?.let(current.tracks.available::savedSelection), command.kind)
            }
        }
        invalidateState()
        }
    }

    override fun handleSetPlaybackParameters(
        playbackParameters: PlaybackParameters,
    ): ListenableFuture<*> {
        if (playbackParameters.pitch != 1f) {
            return failedFuture("Pitch adjustment is not supported by the playback contract.")
        }
        val permille = (playbackParameters.speed * PERMILLE_DIVISOR).roundToInt()
        if (permille !in MIN_RATE_PERMILLE..MAX_RATE_PERMILLE) {
            return failedFuture("Playback speed must be between 0.25x and 4.0x.")
        }
        return launchFuture {
            pendingDefaultRate = null
            dispatchOrThrow(PlayerCommand.SetPlaybackRate(PlaybackRatePermille(permille)))
        }
    }

    override fun handleSetVolume(volume: Float, volumeOperationType: Int): ListenableFuture<*> {
        val percent = VolumePercent((volume * PERCENT_DIVISOR).roundToInt().coerceIn(0, 100))
        return when (volumeOperationType) {
            C.VOLUME_OPERATION_TYPE_MUTE -> dispatch(PlayerCommand.SetMuted(true))
            C.VOLUME_OPERATION_TYPE_UNMUTE -> dispatchAll(
                PlayerCommand.SetVolume(percent),
                PlayerCommand.SetMuted(false),
            )

            else -> if (snapshot.muted && percent != VolumePercent.MUTED) {
                dispatchAll(PlayerCommand.SetVolume(percent), PlayerCommand.SetMuted(false))
            } else {
                dispatch(PlayerCommand.SetVolume(percent))
            }
        }
    }

    override fun handleSetVideoOutput(videoOutput: Any): ListenableFuture<*> {
        if (videoOutput !is Surface) {
            return failedFuture("Only direct Surface output is supported.")
        }
        val token = VideoSurfaceRequests.current()
        return launchFuture {
            if (videoSurfaceRequestToken != token) videoSurfaceSize = null
            videoSurfaceRequestToken = token
            this.videoOutput = videoOutput
            if (!requiresEngineReset()) {
                surfaceLease = engine.surfacePort.attachSurface(videoOutput)
            }
        }
    }

    override fun handleClearVideoOutput(videoOutput: Any?): ListenableFuture<*> {
        return launchFuture {
            if (videoOutput != null && videoOutput !== this.videoOutput) {
                return@launchFuture
            }
            this.videoOutput = null
            videoSurfaceRequestToken = 0L
            videoSurfaceSize = null
            if (!requiresEngineReset()) {
                surfaceLease?.let(engine.surfacePort::detachSurface)
                surfaceLease = null
            }
        }
    }

    internal fun resizeVideoSurface(token: Long, width: Int, height: Int): ListenableFuture<*> = launchFuture {
        require(width in 1..32768 && height in 1..32768) { "Invalid Surface dimensions." }
        if (!VideoSurfaceRequests.isCurrent(token) || token != videoSurfaceRequestToken || videoOutput == null) {
            return@launchFuture
        }
        videoSurfaceSize = width to height
        if (!requiresEngineReset()) surfaceLease?.let { engine.surfacePort.resizeSurface(it, width, height) }
    }

    override fun handleSetMediaItems(
        mediaItems: List<Media3MediaItem>,
        startIndex: Int,
        startPositionMs: Long,
    ): ListenableFuture<*> {
        if (mediaItems.size != 1) {
            return failedFuture("This player accepts one media item at a time.")
        }
        val normalizedIndex = if (startIndex == C.INDEX_UNSET) 0 else startIndex
        val normalizedPosition = if (startPositionMs == C.TIME_UNSET) 0L else startPositionMs
        if (normalizedIndex != 0 || normalizedPosition < 0L) {
            return failedFuture("The requested media start position is invalid.")
        }
        val requestTicket = setMediaRequestFence.begin(
            mediaItems.single().mediaMetadata.extras
                ?.takeIf { it.containsKey(PlaybackRequestMetadata.SEQUENCE_EXTRA) }
                ?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA),
        )
        clearPendingTrackRestore()

        return launchFuture {
            try {
                if (!setMediaRequestFence.isCurrent(requestTicket)) return@launchFuture
                rebuildEngineIfRequired()
                if (!setMediaRequestFence.isCurrent(requestTicket)) return@launchFuture
                val resolved = try {
                    resolveMediaItem(mediaItems.single())
                } catch (failure: Throwable) {
                    if (!setMediaRequestFence.isCurrent(requestTicket)) return@launchFuture
                    throw failure
                }
                if (!setMediaRequestFence.isCurrent(requestTicket)) {
                    runCatching { resolved.descriptor?.close() }
                    return@launchFuture
                }
                try {
                    releaseAudioFocus()
                    dispatchOrThrow(
                        PlayerCommand.SetQueue(
                            items = listOf(resolved.queueItem),
                            startIndex = normalizedIndex,
                            startPosition = Milliseconds(normalizedPosition),
                            // Installing a replacement never inherits playback intent. The client
                            // issues Play only after the same request token is observed as current,
                            // so a superseded slow SAF request cannot start by itself.
                            playWhenReady = false,
                        ),
                    )
                    clearSubtitleDescriptors()
                    runCatching { activeDescriptor?.close() }
                    activeDescriptor = resolved.descriptor
                    activeMediaItem = resolved.mediaItem
                    pendingDefaultRate = resolved.queueItem.id
                    clearPendingTrackRestore()
                    if (preferences.rememberTrackSelection) persistChoice {
                        preferencesProvider?.mediaPlaybackPreferencesRepository?.find(resolved.queueItem.media.id)?.let {
                            if (setMediaRequestFence.isCurrent(requestTicket) && (it.audio != null || it.subtitle != null ||
                                    it.subtitlesDisabled || it.externalSubtitles.isNotEmpty())) {
                                pendingTrackRestore = PendingTrackRestore(resolved.queueItem.id, it, requestTicket, trackIntentGeneration)
                            }
                        }
                    }
                    scheduleTrackRestore()
                    scheduleImportedChoice()
                    invalidateState()
                } catch (failure: Throwable) {
                    runCatching { resolved.descriptor?.close() }
                        .exceptionOrNull()
                        ?.let(failure::addSuppressed)
                    throw failure
                }
            } finally {
                setMediaRequestFence.finish(requestTicket)
            }
        }
    }

    override fun handleSeek(
        mediaItemIndex: Int,
        positionMs: Long,
        seekCommand: Int,
    ): ListenableFuture<*> {
        val snapshot = snapshot
        val currentIndex = snapshot.queue.currentIndex ?: return failedFuture("The queue is empty.")
        val normalizedPosition = if (positionMs == C.TIME_UNSET) 0L else positionMs.coerceAtLeast(0L)
        val targetsDefaultPosition = positionMs == C.TIME_UNSET || positionMs == 0L
        return when {
            mediaItemIndex == currentIndex -> dispatch(
                PlayerCommand.SeekTo(Milliseconds(normalizedPosition)),
            )

            mediaItemIndex == currentIndex + 1 && targetsDefaultPosition ->
                dispatch(PlayerCommand.SkipNext)

            mediaItemIndex == currentIndex - 1 && targetsDefaultPosition ->
                dispatch(PlayerCommand.SkipPrevious)

            mediaItemIndex in snapshot.queue.items.indices -> dispatch(
                PlayerCommand.SetQueue(
                    items = snapshot.queue.items,
                    startIndex = mediaItemIndex,
                    startPosition = Milliseconds(normalizedPosition),
                    playWhenReady = snapshot.playWhenReady,
                ),
            )

            else -> failedFuture("The requested media item index is invalid.")
        }
    }

    internal fun shutdownAsync(): ListenableFuture<*> {
        setMediaRequestFence.invalidate()
        subtitleRequestGeneration++
        ++trackIntentGeneration
        clearPendingTrackRestore()
        pendingImportedChoice = null
        releaseAudioFocus()
        return launchFuture(allowAfterShutdown = true) {
            shutdown()
        }
    }

    private suspend fun shutdown() {
        if (!shutdownStarted.compareAndSet(false, true)) {
            shutdownCompletion.await()
            return
        }
        configurationJob?.cancel()

        var shutdownFailure: Throwable? = null
        val leaseToDetach = surfaceLease
        surfaceLease = null
        val engineToClose = engine
        try {
            runCatching {
                withContext(NonCancellable) {
                    progressRecorder.closeAndFlush(
                        epoch = engineEpoch,
                        snapshot = engineToClose.session.snapshot.value,
                    )
                }
            }.exceptionOrNull()?.let { shutdownFailure = it }
            withContext(Dispatchers.IO) {
                leaseToDetach?.let { lease ->
                    runCatching { engineToClose.surfacePort.detachSurface(lease) }
                        .exceptionOrNull()
                        ?.let { failure ->
                            shutdownFailure?.addSuppressed(failure)
                                ?: run { shutdownFailure = failure }
                        }
                }
                try {
                    engineToClose.session.close()
                } catch (failure: Throwable) {
                    shutdownFailure?.addSuppressed(failure) ?: run { shutdownFailure = failure }
                }
            }
        } catch (failure: Throwable) {
            shutdownFailure?.addSuppressed(failure) ?: run { shutdownFailure = failure }
        } finally {
            snapshotCollector.cancel()
            eventCollector.cancel()
            runCatching { activeDescriptor?.close() }
                .exceptionOrNull()
                ?.let { failure ->
                    shutdownFailure?.addSuppressed(failure) ?: run { shutdownFailure = failure }
                }
            activeDescriptor = null
            clearSubtitleDescriptors()
            activeMediaItem = null
            videoOutput = null
            shutdownFailure?.let(shutdownCompletion::completeExceptionally)
                ?: shutdownCompletion.complete(Unit)
        }
        shutdownFailure?.let { throw it }
    }

    private fun startSnapshotCollector(observedEngine: PlaybackEngine, observedEpoch: Long): Job =
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            observedEngine.session.snapshot.collect { next ->
                if (engine === observedEngine && engineEpoch == observedEpoch) {
                    snapshot = next
                    if (next.status in setOf(PlayerStatus.IDLE, PlayerStatus.ENDED, PlayerStatus.ERROR, PlayerStatus.CLOSED)) {
                        releaseAudioFocus()
                    } else {
                        audioFocus.setNoisyEnabled(next.playWhenReady)
                        if (next.status == PlayerStatus.PLAYING) onPlaybackPublished()
                    }
                    progressRecorder.observeSnapshot(observedEpoch, next)
                    scheduleTrackRestore()
                    scheduleImportedChoice()
                    invalidateState()
                }
            }
        }

    private fun startEventCollector(observedEngine: PlaybackEngine, observedEpoch: Long): Job =
        scope.launch(
            context = Dispatchers.Unconfined,
            start = CoroutineStart.UNDISPATCHED,
        ) {
            observedEngine.session.events.collect { event ->
                // DefaultPlayerSession emits from one serialized actor. Inline forwarding keeps
                // StateChanged + ItemTransition ordered ahead of command completion; the epoch
                // fence makes late events from a detached engine harmless.
                progressRecorder.observeEvent(observedEpoch, event)
            }
        }

    private suspend fun rebuildEngineIfRequired() {
        val authoritativeSnapshot = engine.session.snapshot.value
        if (authoritativeSnapshot.error?.recovery != ErrorRecovery.RESET) {
            return
        }
        snapshot = authoritativeSnapshot
        releaseAudioFocus()

        val previousEngine = engine
        val previousEpoch = engineEpoch
        progressRecorder.flushAndDetach(previousEpoch, authoritativeSnapshot)
        snapshotCollector.cancelAndJoin()
        eventCollector.cancelAndJoin()
        withContext(Dispatchers.IO) {
            previousEngine.session.close()
        }
        runCatching { activeDescriptor?.close() }
        activeDescriptor = null
        clearSubtitleDescriptors()
        activeMediaItem = null
        surfaceLease = null

        val replacement = engineFactory()
        engine = replacement
        applyPlaybackConfiguration()
        engineEpoch = previousEpoch + 1L
        snapshot = replacement.session.snapshot.value
        progressRecorder.bind(engineEpoch, snapshot)
        snapshotCollector = startSnapshotCollector(replacement, engineEpoch)
        eventCollector = startEventCollector(replacement, engineEpoch)
        val surface = videoOutput as? Surface
        if (surface?.isValid == true) {
            surfaceLease = replacement.surfacePort.attachSurface(surface)
            videoSurfaceSize?.let { (width, height) ->
                replacement.surfacePort.resizeSurface(checkNotNull(surfaceLease), width, height)
            }
        }
        invalidateState()
    }

    private fun requiresEngineReset(): Boolean =
        engine.session.snapshot.value.error?.recovery == ErrorRecovery.RESET

    private suspend fun applyPlaybackConfiguration() {
        val port = engine.configurationPort ?: return
        try {
            val paths = preferencesProvider?.resolvePlaybackResources(preferences.options) ?: PlaybackResourceLocations()
            withContext(Dispatchers.IO) { port.configure(preferences.options, LibmpvResourcePaths(paths.fontsDirectory, paths.shaderFiles)) }
            configurationMessage = null
        } catch (failure: CancellationException) { throw failure
        } catch (failure: Exception) {
            configurationMessage = "部分播放设置未能应用：${failure.message}"
            Log.w("ZivMedia3Player", "Playback settings were not applied.", failure)
        }
        onConfigurationStatus(configurationMessage)
    }

    internal fun readDiagnostics(): ListenableFuture<PlaybackDiagnostics> {
        val reply = SettableFuture.create<PlaybackDiagnostics>()
        val operation = launchFuture {
            val port = engine.configurationPort
            reply.set(if (port == null) PlaybackDiagnostics() else withContext(Dispatchers.IO) { port.readDiagnostics() })
        }
        operation.addListener({ runCatching { operation.get() }.exceptionOrNull()?.let { reply.setException(it) } },
            com.google.common.util.concurrent.MoreExecutors.directExecutor())
        return reply
    }

    internal fun addSubtitle(
        uri: Uri,
        expectedMediaId: String,
        expectedRequestSequence: Long,
        title: String?,
    ): ListenableFuture<*> {
        if (uri.scheme != CONTENT_SCHEME) return failedFuture("Choose a subtitle through the document picker.")
        val request = ++subtitleRequestGeneration
        val intent = ++trackIntentGeneration
        clearPendingTrackRestore()
        pendingImportedChoice = null
        val epoch = engineEpoch
        val itemId = snapshot.queue.currentItem?.id
        fun isCurrent(): Boolean = intent == trackIntentGeneration && request == subtitleRequestGeneration && epoch == engineEpoch &&
            itemId != null && engine.session.snapshot.value.queue.currentItem?.id == itemId &&
            activeMediaItem?.mediaId == expectedMediaId &&
            activeMediaItem?.mediaMetadata?.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA) == expectedRequestSequence &&
            !setMediaRequestFence.hasPendingRequest()
        return launchFuture {
            check(isCurrent()) { "The selected media changed while choosing a subtitle." }
            clearPendingTrackRestore()
            pendingImportedChoice = null
            check(subtitleDescriptors.size < 16) { "At most 16 external subtitles may be attached." }
            val managed = preferencesProvider?.takeIf { it.mediaPlaybackPreferencesRepository != null }
                ?.importSubtitleResource(uri)
            check(isCurrent()) { "The selected media changed while importing a subtitle." }
            val (descriptor, displayName) = withContext(NonCancellable + Dispatchers.IO) {
                val displayName = managed?.title ?: title?.takeIf(String::isNotBlank) ?: runCatching {
                    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                        if (cursor.moveToFirst()) cursor.getString(0)?.takeIf(String::isNotBlank) else null
                    }
                }.getOrNull() ?: uri.lastPathSegment ?: "External subtitle"
                checkNotNull(context.contentResolver.openFileDescriptor(managed?.uri ?: uri, READ_ONLY_MODE)) {
                    "The subtitle could not be opened."
                } to displayName
            }
            try {
                check(isCurrent()) { "The selected media changed while opening a subtitle." }
                dispatchOrThrow(PlayerCommand.AddSubtitle(SubtitleSource(
                    "$FILE_DESCRIPTOR_URI_PREFIX${descriptor.fd}", displayName,
                )))
                subtitleDescriptors += descriptor
                if (managed != null) persistChoice {
                    preferencesProvider.mediaPlaybackPreferencesRepository?.saveSubtitle(MediaId(expectedMediaId),
                        SavedSubtitle(managed.id, displayName))
                    if (intent == trackIntentGeneration) pendingImportedChoice = PendingImportedChoice(checkNotNull(itemId), displayName, intent)
                    scheduleImportedChoice()
                }
            } catch (failure: Throwable) {
                runCatching { descriptor.close() }.exceptionOrNull()?.let(failure::addSuppressed)
                throw failure
            }
        }
    }

    private fun clearSubtitleDescriptors() {
        subtitleRequestGeneration++
        clearPendingTrackRestore()
        pendingImportedChoice = null
        subtitleDescriptors.forEach { descriptor ->
            runCatching { descriptor.close() }.onFailure { Log.w("ZivMedia3Player", "Subtitle handle could not be closed.", it) }
        }
        subtitleDescriptors.clear()
    }

    private suspend fun persistChoice(block: suspend () -> Unit): Boolean {
        try { block(); return true } catch (failure: CancellationException) { throw failure
        } catch (failure: Exception) {
            configurationMessage = "音轨或字幕记忆未能保存/读取：${failure.message}"
            onConfigurationStatus(configurationMessage)
            Log.w("ZivMedia3Player", "Playback choices could not be persisted/restored.", failure)
            return false
        }
    }

    private fun clearPendingTrackRestore() {
        pendingTrackRestore?.completion?.complete(Unit)
        pendingTrackRestore = null
    }

    private fun PendingTrackRestore.isCurrent(): Boolean = pendingTrackRestore === this &&
        preferences.rememberTrackSelection && intent == trackIntentGeneration && setMediaRequestFence.isCurrent(ticket) &&
        engine.session.snapshot.value.queue.currentItem?.id == itemId

    private suspend fun awaitInitialTrackRestore(pending: PendingTrackRestore) {
        val observedEngine = engine
        // Keep autoplay false during initial preparation. Native track discovery can lag
        // FILE_LOADED, so wait for the restore independently of the command mutex.
        if (!awaitPreparationOrRestoreCancellation(pending.completion) {
                observedEngine.session.snapshot.first {
                    it.queue.currentItem?.id != pending.itemId || it.status != PlayerStatus.LOADING
                }
            }) return
        if (!pending.isCurrent()) return
        if (observedEngine.session.snapshot.value.status !in setOf(
                PlayerStatus.READY, PlayerStatus.PLAYING, PlayerStatus.PAUSED, PlayerStatus.BUFFERING)) {
            clearPendingTrackRestore()
            return
        }
        scheduleTrackRestore()
        if (withTimeoutOrNull(2_000L) { pending.completion.await(); true } != true) {
            if (pendingTrackRestore === pending) {
                clearPendingTrackRestore()
                configurationMessage = "保存的部分轨道尚未出现，已使用当前可用轨道。"
                onConfigurationStatus(configurationMessage)
            }
        }
    }

    private fun scheduleImportedChoice() {
        val pending = pendingImportedChoice ?: return
        val current = engine.session.snapshot.value
        if (current.queue.currentItem?.id != pending.itemId || pending.intent != trackIntentGeneration) { pendingImportedChoice = null; return }
        val selected = current.tracks.selected[TrackKind.SUBTITLE] ?: return
        val choice = current.tracks.available.savedSelection(selected)
            ?.takeIf { it.external && it.label == pending.title } ?: return
        pendingImportedChoice = null
        scope.launch {
            operationGate.withLock {
                val latest = engine.session.snapshot.value
                if (pending.intent == trackIntentGeneration && latest.queue.currentItem?.id == pending.itemId &&
                    latest.tracks.selected[TrackKind.SUBTITLE] == selected) persistChoice {
                    preferencesProvider?.mediaPlaybackPreferencesRepository?.saveTrack(
                        checkNotNull(current.queue.currentItem).media.id, choice, TrackKind.SUBTITLE)
                }
            }
        }
    }

    private fun scheduleTrackRestore() {
        val pending = pendingTrackRestore ?: return
        if (restoringTracks || shutdownStarted.get()) return
        if (!pending.isCurrent()) { clearPendingTrackRestore(); return }
        val current = engine.session.snapshot.value
        if (current.queue.currentItem?.id != pending.itemId || current.status !in setOf(
                PlayerStatus.READY, PlayerStatus.PLAYING, PlayerStatus.PAUSED, PlayerStatus.BUFFERING)) return
        restoringTracks = true
        scope.launch {
            val beforeTracks = engine.session.snapshot.value.tracks
            try {
                val restored = persistChoice {
                    while (pending.externalIndex < pending.saved.externalSubtitles.size) {
                        if (!pending.isCurrent()) return@persistChoice
                        val saved = pending.saved.externalSubtitles[pending.externalIndex]
                        val resource = preferencesProvider?.findSubtitleResource(saved.resourceId)
                        if (resource == null) { pending.externalIndex++; continue }
                        if (!pending.isCurrent()) return@persistChoice
                        // Provider and database I/O must not hold up Pause, replacement,
                        // shutdown, or the two-second best-effort restoration deadline.
                        val descriptor = withContext(NonCancellable + Dispatchers.IO) {
                            checkNotNull(context.contentResolver.openFileDescriptor(resource.uri, READ_ONLY_MODE))
                        }
                        var retained = false
                        try {
                            operationGate.withLock {
                                if (pending.isCurrent()) {
                                    dispatchOrThrow(PlayerCommand.AddSubtitle(SubtitleSource(
                                        "$FILE_DESCRIPTOR_URI_PREFIX${descriptor.fd}", saved.title)))
                                    subtitleDescriptors += descriptor
                                    retained = true
                                    pending.externalIndex++
                                }
                            }
                        } finally { if (!retained) runCatching { descriptor.close() } }
                        if (!pending.isCurrent()) return@persistChoice
                    }
                    operationGate.withLock {
                        if (!pending.isCurrent()) return@withLock
                        val tracks = engine.session.snapshot.value.tracks.available
                        if (!pending.audioRestored && pending.isCurrent()) {
                            pending.saved.audio?.matchTrack(tracks)?.let {
                                dispatchOrThrow(PlayerCommand.SelectTrack(TrackKind.AUDIO, it))
                                pending.audioRestored = true
                            }
                        }
                        if (!pending.subtitleRestored && pending.isCurrent()) {
                            if (pending.saved.subtitlesDisabled) {
                                dispatchOrThrow(PlayerCommand.SelectTrack(TrackKind.SUBTITLE, null))
                                pending.subtitleRestored = true
                            } else pending.saved.subtitle?.matchTrack(tracks)?.let {
                                dispatchOrThrow(PlayerCommand.SelectTrack(TrackKind.SUBTITLE, it))
                                pending.subtitleRestored = true
                            }
                        }
                        if (pending.audioRestored && pending.subtitleRestored && pendingTrackRestore === pending)
                            clearPendingTrackRestore()
                    }
                }
                if (!restored && pendingTrackRestore === pending) clearPendingTrackRestore()
            } finally {
                restoringTracks = false
                if (pendingTrackRestore !== pending || beforeTracks != engine.session.snapshot.value.tracks) scheduleTrackRestore()
            }
        }
    }

    private class PendingTrackRestore(val itemId: QueueItemId, val saved: MediaPlaybackPreferences,
        val ticket: SetMediaRequestFence.Ticket, val intent: Long) {
        val completion = CompletableDeferred<Unit>()
        var externalIndex = 0
        var audioRestored = saved.audio == null
        var subtitleRestored = saved.subtitle == null && !saved.subtitlesDisabled
    }

    private data class PendingImportedChoice(val itemId: QueueItemId, val title: String, val intent: Long)

    private fun releaseAudioFocus() {
        audioFocusPolicy.cancelPlaybackIntent()
        audioFocus.abandon()
        cancelPendingForeground()
    }

    private fun onAudioFocusChanged(token: Long, change: PlaybackFocusChange) {
        val future = launchFuture {
            if (!audioFocus.isCurrent(token)) return@launchFuture
            playWhenReadyChangeReason = if (change == PlaybackFocusChange.NOISY) {
                Player.PLAY_WHEN_READY_CHANGE_REASON_AUDIO_BECOMING_NOISY
            } else {
                Player.PLAY_WHEN_READY_CHANGE_REASON_AUDIO_FOCUS_LOSS
            }
            when (audioFocusPolicy.onChange(change)) {
                PlaybackFocusAction.NONE -> Unit
                PlaybackFocusAction.PAUSE, PlaybackFocusAction.PAUSE_AND_ABANDON -> {
                    if (change == PlaybackFocusChange.LOSS || change == PlaybackFocusChange.NOISY) releaseAudioFocus()
                    if (PlayerCapability.PAUSE in engine.session.snapshot.value.capabilities.available) {
                        dispatchOrThrow(PlayerCommand.Pause)
                        progressRecorder.flushPause(engineEpoch, engine.session.snapshot.value)
                    }
                }
                PlaybackFocusAction.RESUME -> {
                    if (PlayerCapability.PLAY in engine.session.snapshot.value.capabilities.available) {
                        ensurePlaybackForeground()
                        dispatchOrThrow(PlayerCommand.Play)
                    } else {
                        releaseAudioFocus()
                    }
                }
            }
        }
        future.addListener({
            runCatching { future.get() }.onFailure {
                releaseAudioFocus()
                Log.w("ZivMedia3Player", "Audio interruption could not be applied.", it)
            }
        }, java.util.concurrent.Executor { applicationHandler.post(it) })
    }

    private suspend fun resolveMediaItem(mediaItem: Media3MediaItem): ResolvedMediaItem {
        val configuration = checkNotNull(mediaItem.localConfiguration) {
            "The media item has no URI."
        }
        val uri = configuration.uri
        val descriptor = if (uri.scheme == CONTENT_SCHEME) {
            withContext(Dispatchers.IO) {
                checkNotNull(context.contentResolver.openFileDescriptor(uri, READ_ONLY_MODE)) {
                    "The selected media could not be opened."
                }
            }
        } else {
            null
        }
        try {
            val locator = descriptor?.let { "$FILE_DESCRIPTOR_URI_PREFIX${it.fd}" } ?: uri.toString()
            val queueItemId = queueItemIdGenerator.next()
            val mediaId = mediaItem.mediaId.ifBlank { "session:${queueItemId.value}" }
            val normalizedItem = mediaItem.buildUpon().setMediaId(mediaId).build()
            return ResolvedMediaItem(
                queueItem = QueueItem(
                    id = queueItemId,
                    media = MediaItem(
                        id = MediaId(mediaId),
                        source = MediaSource(locator, configuration.mimeType),
                        metadata = MediaMetadata(
                            title = mediaItem.mediaMetadata.title?.toString(),
                            artist = mediaItem.mediaMetadata.artist?.toString(),
                            album = mediaItem.mediaMetadata.albumTitle?.toString(),
                            artworkLocator = mediaItem.mediaMetadata.artworkUri?.toString(),
                        ),
                    ),
                ),
                mediaItem = normalizedItem,
                descriptor = descriptor,
            )
        } catch (failure: Throwable) {
            runCatching { descriptor?.close() }
                .exceptionOrNull()
                ?.let(failure::addSuppressed)
            throw failure
        }
    }

    private fun dispatch(command: PlayerCommand): ListenableFuture<*> = dispatchAll(command)

    private fun dispatchAll(vararg commands: PlayerCommand): ListenableFuture<*> = launchFuture {
        commands.forEach { dispatchOrThrow(it) }
    }

    private suspend fun dispatchOrThrow(command: PlayerCommand) {
        when (val result = engine.session.dispatch(command)) {
            is CommandResult.Accepted -> Unit
            is CommandResult.Rejected -> throw result.error.toPlaybackException()
            is CommandResult.Failed -> throw result.error.toPlaybackException()
        }
    }

    private fun launchFuture(
        allowAfterShutdown: Boolean = false,
        beforeOperation: suspend () -> Unit = {},
        block: suspend () -> Unit,
    ): ListenableFuture<*> {
        if (shutdownStarted.get() && !allowAfterShutdown) {
            return failedFuture("The playback service is shutting down.")
        }
        val future = SettableFuture.create<Void>()
        val job = scope.launch {
            try {
                if (!allowAfterShutdown) configurationReady.await()
                beforeOperation()
                operationGate.withLock {
                    if (shutdownStarted.get() && !allowAfterShutdown) {
                        throw PlaybackException(
                            "The playback service is shutting down.",
                            null,
                            PlaybackException.ERROR_CODE_UNSPECIFIED,
                        )
                    }
                    block()
                }
                future.set(null)
            } catch (failure: Throwable) {
                future.setException(failure)
            }
        }
        job.invokeOnCompletion { failure ->
            if (!future.isDone) {
                future.setException(
                    failure ?: CancellationException("The playback operation was cancelled."),
                )
            }
        }
        return future
    }

    private fun failedFuture(message: String): ListenableFuture<*> =
        Futures.immediateFailedFuture<Void>(
            PlaybackException(message, null, PlaybackException.ERROR_CODE_UNSPECIFIED),
        )

    private fun failedFuture(failure: Throwable): ListenableFuture<*> =
        Futures.immediateFailedFuture<Void>(failure)

    private data class ResolvedMediaItem(
        val queueItem: QueueItem,
        val mediaItem: Media3MediaItem,
        val descriptor: ParcelFileDescriptor?,
    )

    private companion object {
        val PLAYBACK_AUDIO_ATTRIBUTES = AudioAttributes.Builder()
            .setUsage(C.USAGE_MEDIA)
            .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
            .build()
        const val PERCENT_DIVISOR = 100f
        const val PERMILLE_DIVISOR = 1_000f
        const val MIN_RATE_PERMILLE = 250
        const val MAX_RATE_PERMILLE = 4_000
        const val CONTENT_SCHEME = "content"
        const val READ_ONLY_MODE = "r"
        // Borrow the granted descriptor. Reopening /proc/self/fd can fail Android access checks;
        // fdclose:// would instead transfer ownership and break replay and our PFD cleanup.
        const val FILE_DESCRIPTOR_URI_PREFIX = "fd://"
    }
}

internal fun PlaybackSnapshot.availableMedia3Commands(): Player.Commands {
    val builder = Player.Commands.Builder().addAllReadOnlyCommands()
    media3CommandPolicy().forEach(builder::add)
    return builder.build()
}

internal fun PlaybackSnapshot.media3CommandPolicy(): Set<Int> {
    val available = capabilities.available
    val resetRequired = error?.recovery == ErrorRecovery.RESET
    return buildSet {
        add(Player.COMMAND_RELEASE)

        if (status != PlayerStatus.CLOSED) {
            add(Player.COMMAND_SET_MEDIA_ITEM)
            add(Player.COMMAND_SET_VIDEO_SURFACE)
            if (!resetRequired) {
                add(Player.COMMAND_PREPARE)
            }
        }
        val requiredPlayPauseCapability = if (playWhenReady) {
            PlayerCapability.PAUSE
        } else {
            PlayerCapability.PLAY
        }
        if (requiredPlayPauseCapability in available) {
            add(Player.COMMAND_PLAY_PAUSE)
        }
        if (PlayerCapability.STOP in available) {
            add(Player.COMMAND_STOP)
        }
        if (PlayerCapability.SEEK in available) {
            add(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM)
            add(Player.COMMAND_SEEK_TO_DEFAULT_POSITION)
            add(Player.COMMAND_SEEK_TO_MEDIA_ITEM)
        }
        if (PlayerCapability.SKIP_NEXT in available) {
            add(Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM)
            add(Player.COMMAND_SEEK_TO_NEXT)
        }
        if (PlayerCapability.SKIP_PREVIOUS in available) {
            add(Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM)
            add(Player.COMMAND_SEEK_TO_PREVIOUS)
        }
        if (PlayerCapability.SET_REPEAT in available) {
            add(Player.COMMAND_SET_REPEAT_MODE)
        }
        if (PlayerCapability.SET_RATE in available) {
            add(Player.COMMAND_SET_SPEED_AND_PITCH)
        }
        if (
            PlayerCapability.SET_VOLUME in available &&
            PlayerCapability.SET_MUTED in available
        ) {
            add(Player.COMMAND_SET_VOLUME)
        }
        if (PlayerCapability.SELECT_TRACK in available) add(Player.COMMAND_SET_TRACK_SELECTION_PARAMETERS)
    }
}

private fun PlaybackSnapshot.toMedia3Playlist(
    retainedMediaItem: Media3MediaItem?,
): List<SimpleBasePlayer.MediaItemData> = queue.items.mapIndexed { index, item ->
    val isCurrent = index == queue.currentIndex
    val mediaItem = retainedMediaItem
        ?.takeIf { it.mediaId == item.media.id.value }
        ?: item.toMedia3MediaItem()
    SimpleBasePlayer.MediaItemData.Builder(item.id.value)
        .setMediaItem(mediaItem)
        .setMediaMetadata(mediaItem.mediaMetadata)
        .setIsSeekable(PlayerCapability.SEEK in capabilities.available)
        .setTracks(if (isCurrent) tracks.toMedia3Tracks(item.id.value) else Tracks.EMPTY)
        .setDurationUs(
            if (isCurrent) {
                timeline.duration?.value
                    ?.takeIf { it <= Long.MAX_VALUE / MILLIS_TO_MICROS }
                    ?.times(MILLIS_TO_MICROS)
                    ?: C.TIME_UNSET
            } else {
                C.TIME_UNSET
            },
        )
        .build()
}

private fun QueueItem.toMedia3MediaItem(): Media3MediaItem = Media3MediaItem.Builder()
    .setMediaId(media.id.value)
    .setUri(media.source.locator)
    .apply { media.source.mimeType?.let(::setMimeType) }
    .setMediaMetadata(media.metadata.toMedia3Metadata())
    .build()

private fun MediaMetadata.toMedia3Metadata(): Media3MediaMetadata = Media3MediaMetadata.Builder()
    .apply {
        title?.let(::setTitle)
        artist?.let(::setArtist)
        album?.let(::setAlbumTitle)
        artworkLocator?.let { setArtworkUri(it.toUri()) }
    }
    .build()

private fun RepeatMode.toMedia3RepeatMode(): Int = when (this) {
    RepeatMode.OFF -> Player.REPEAT_MODE_OFF
    RepeatMode.ONE -> Player.REPEAT_MODE_ONE
    RepeatMode.ALL -> Player.REPEAT_MODE_ALL
}

private fun Int.toCoreRepeatMode(): RepeatMode = when (this) {
    Player.REPEAT_MODE_ONE -> RepeatMode.ONE
    Player.REPEAT_MODE_ALL -> RepeatMode.ALL
    else -> RepeatMode.OFF
}

private fun PlayerError.toPlaybackException(): PlaybackException = PlaybackException(
    message,
    null,
    PlaybackException.ERROR_CODE_UNSPECIFIED,
)
