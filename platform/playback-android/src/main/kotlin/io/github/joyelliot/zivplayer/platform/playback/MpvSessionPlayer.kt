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
import android.view.Surface
import androidx.media3.common.C
import androidx.media3.common.MediaItem as Media3MediaItem
import androidx.media3.common.MediaMetadata as Media3MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.common.SimpleBasePlayer
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
import io.github.joyelliot.zivplayer.core.model.VolumePercent
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
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.UUID
import kotlin.math.roundToInt

private const val MILLIS_TO_MICROS = 1_000L

internal data class PlaybackEngine(
    val session: PlayerSession,
    val surfacePort: LibmpvSurfacePort,
)

internal class QueueItemIdGenerator(
    private val instanceId: String = UUID.randomUUID().toString(),
) {
    private val sequence = AtomicLong(0L)

    fun next(): QueueItemId = QueueItemId("queue:$instanceId:${sequence.getAndIncrement()}")
}

internal class MpvSessionPlayer(
    applicationLooper: Looper,
    private val context: Context,
    private val queueItemIdGenerator: QueueItemIdGenerator = QueueItemIdGenerator(),
    private val progressRecorder: PlaybackProgressRecorder,
    private val engineFactory: () -> PlaybackEngine,
) : SimpleBasePlayer(applicationLooper) {
    private val scope = CoroutineScope(
        SupervisorJob() + Handler(applicationLooper).asCoroutineDispatcher("ZivMedia3Player"),
    )
    private val shutdownStarted = AtomicBoolean(false)
    private val shutdownCompletion = CompletableDeferred<Unit>()
    private val operationGate = Mutex()

    private var engine = engineFactory()
    private var engineEpoch = 0L

    @Volatile
    private var snapshot: PlaybackSnapshot = engine.session.snapshot.value

    private var videoOutput: Any? = null
    private var surfaceLease: LibmpvSurfaceLease? = null
    private var activeDescriptor: ParcelFileDescriptor? = null
    private var activeMediaItem: Media3MediaItem? = null
    private var snapshotCollector: Job
    private var eventCollector: Job

    init {
        progressRecorder.bind(engineEpoch, snapshot)
        snapshotCollector = startSnapshotCollector(engine, engineEpoch)
        eventCollector = startEventCollector(engine, engineEpoch)
    }

    override fun getState(): State {
        val snapshot = snapshot
        val projection = snapshot.toMedia3Projection()
        val volume = snapshot.volume.value / PERCENT_DIVISOR
        val builder = State.Builder()
            .setAvailableCommands(snapshot.availableMedia3Commands())
            .setPlayWhenReady(
                snapshot.playWhenReady,
                Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST,
            )
            .setPlaybackState(projection.playbackState)
            .setIsLoading(projection.isLoading)
            .setRepeatMode(snapshot.repeatMode.toMedia3RepeatMode())
            .setPlaybackParameters(
                PlaybackParameters(snapshot.playbackRate.value / PERMILLE_DIVISOR),
            )
            .setVolume(if (snapshot.muted) 0f else volume)
            .setUnmuteVolume(volume)

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

    override fun handleSetPlayWhenReady(playWhenReady: Boolean): ListenableFuture<*> = launchFuture {
        dispatchOrThrow(if (playWhenReady) PlayerCommand.Play else PlayerCommand.Pause)
        if (!playWhenReady) {
            progressRecorder.flushPause(engineEpoch, engine.session.snapshot.value)
        }
    }

    override fun handlePrepare(): ListenableFuture<*> = Futures.immediateVoidFuture()

    override fun handleStop(): ListenableFuture<*> = launchFuture {
        val beforeStop = engine.session.snapshot.value
        dispatchOrThrow(PlayerCommand.Stop)
        if (beforeStop.queue.currentItem != null && beforeStop.status != PlayerStatus.IDLE) {
            progressRecorder.flushStop(engineEpoch, engine.session.snapshot.value)
        }
    }

    override fun handleRelease(): ListenableFuture<*> = shutdownAsync()

    override fun handleSetRepeatMode(repeatMode: Int): ListenableFuture<*> = dispatch(
        PlayerCommand.SetRepeatMode(repeatMode.toCoreRepeatMode()),
    )

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
        return dispatch(PlayerCommand.SetPlaybackRate(PlaybackRatePermille(permille)))
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
        return launchFuture {
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
            if (!requiresEngineReset()) {
                surfaceLease?.let(engine.surfacePort::detachSurface)
                surfaceLease = null
            }
        }
    }

    override fun handleSetMediaItems(
        mediaItems: List<Media3MediaItem>,
        startIndex: Int,
        startPositionMs: Long,
    ): ListenableFuture<*> {
        if (mediaItems.size != 1) {
            return failedFuture("This bootstrap adapter accepts one media item at a time.")
        }
        val normalizedIndex = if (startIndex == C.INDEX_UNSET) 0 else startIndex
        val normalizedPosition = if (startPositionMs == C.TIME_UNSET) 0L else startPositionMs
        if (normalizedIndex != 0 || normalizedPosition < 0L) {
            return failedFuture("The requested media start position is invalid.")
        }

        return launchFuture {
            rebuildEngineIfRequired()
            val resolved = resolveMediaItem(mediaItems.single())
            try {
                dispatchOrThrow(
                    PlayerCommand.SetQueue(
                        items = listOf(resolved.queueItem),
                        startIndex = normalizedIndex,
                        startPosition = Milliseconds(normalizedPosition),
                        playWhenReady = snapshot.playWhenReady,
                    ),
                )
                runCatching { activeDescriptor?.close() }
                activeDescriptor = resolved.descriptor
                activeMediaItem = resolved.mediaItem
                invalidateState()
            } catch (failure: Throwable) {
                runCatching { resolved.descriptor?.close() }
                    .exceptionOrNull()
                    ?.let(failure::addSuppressed)
                throw failure
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

    internal fun shutdownAsync(): ListenableFuture<*> = launchFuture(allowAfterShutdown = true) {
        shutdown()
    }

    private suspend fun shutdown() {
        if (!shutdownStarted.compareAndSet(false, true)) {
            shutdownCompletion.await()
            return
        }

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
                    progressRecorder.observeSnapshot(observedEpoch, next)
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
        activeMediaItem = null
        surfaceLease = null

        val replacement = engineFactory()
        engine = replacement
        engineEpoch = previousEpoch + 1L
        snapshot = replacement.session.snapshot.value
        progressRecorder.bind(engineEpoch, snapshot)
        snapshotCollector = startSnapshotCollector(replacement, engineEpoch)
        eventCollector = startEventCollector(replacement, engineEpoch)
        val surface = videoOutput as? Surface
        if (surface?.isValid == true) {
            surfaceLease = replacement.surfacePort.attachSurface(surface)
        }
        invalidateState()
    }

    private fun requiresEngineReset(): Boolean =
        engine.session.snapshot.value.error?.recovery == ErrorRecovery.RESET

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
            val locator = descriptor?.let { "$FILE_DESCRIPTOR_PATH_PREFIX${it.fd}" } ?: uri.toString()
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
        block: suspend () -> Unit,
    ): ListenableFuture<*> {
        if (shutdownStarted.get() && !allowAfterShutdown) {
            return failedFuture("The playback service is shutting down.")
        }
        val future = SettableFuture.create<Void>()
        val job = scope.launch {
            try {
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
        const val PERCENT_DIVISOR = 100f
        const val PERMILLE_DIVISOR = 1_000f
        const val MIN_RATE_PERMILLE = 250
        const val MAX_RATE_PERMILLE = 4_000
        const val CONTENT_SCHEME = "content"
        const val READ_ONLY_MODE = "r"
        const val FILE_DESCRIPTOR_PATH_PREFIX = "/proc/self/fd/"
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
        artworkLocator?.let { setArtworkUri(Uri.parse(it)) }
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
