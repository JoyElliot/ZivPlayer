// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer

import android.app.Application
import android.content.ComponentName
import android.os.Looper
import android.os.Bundle
import android.net.Uri
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.C
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import io.github.joyelliot.zivplayer.feature.player.PlayerConnectionStatus
import io.github.joyelliot.zivplayer.feature.player.PlayerPlaybackStatus
import io.github.joyelliot.zivplayer.feature.player.PlayerRepeatMode
import io.github.joyelliot.zivplayer.feature.player.PlayerUiState
import io.github.joyelliot.zivplayer.feature.player.PlayerTrackKind
import io.github.joyelliot.zivplayer.feature.player.PlayerTrackUiItem
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.SubtitleRequestContract
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Owns the application-scoped MediaController across Activity recreation.
 *
 * A disconnected controller is terminal in Media3. Each reconnect therefore creates a fresh
 * controller and uses a generation fence so late futures and callbacks cannot replace it.
 */
class PlaybackControllerViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val applicationContext = application.applicationContext
    private val mainExecutor = ContextCompat.getMainExecutor(applicationContext)
    private val sessionToken = SessionToken(
        applicationContext,
        ComponentName(applicationContext, PlaybackService::class.java),
    )

    private val mutableController = mutableStateOf<MediaController?>(null)
    val controller: State<MediaController?> = mutableController

    private val mutablePlayerState = mutableStateOf(PlayerUiState())
    val playerState: State<PlayerUiState> = mutablePlayerState

    private var controllerFuture: ListenableFuture<MediaController>? = null
    private var connectionGeneration = 0L
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private var positionTickerJob: Job? = null
    private var cleared = false
    private var lastCompletedMediaId: String? = null
    private var subtitleTarget: Pair<String, Long>? = null
    private var subtitleMessage: String? = null
    private var subtitleMessageMediaId: String? = null
    private var subtitleMessageSequence: Long? = null
    private var subtitleOperationGeneration = 0L

    private val playerListener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) {
            refreshState(player)
        }
    }

    init {
        connect(reconnecting = false)
    }

    fun togglePlayPause() = withControllerCommand(Player.COMMAND_PLAY_PAUSE) { active ->
        if (active.playbackState == Player.STATE_ENDED &&
            active.isCommandAvailable(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM)
        ) {
            active.seekTo(0L)
        }
        if (active.isPlaying || active.playWhenReady) {
            active.pause()
        } else {
            active.play()
        }
    }

    fun stop() = withControllerCommand(Player.COMMAND_STOP, MediaController::stop)

    fun seekTo(positionMs: Long) =
        withControllerCommand(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM) { active ->
            active.seekTo(positionMs.coerceAtLeast(0L))
        }

    fun setPlaybackSpeed(speed: Float) =
        withControllerCommand(Player.COMMAND_SET_SPEED_AND_PITCH) { active ->
            if (speed.isFinite()) {
                active.setPlaybackSpeed(speed.coerceIn(MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED))
            }
        }

    fun setVolume(volume: Float) = withControllerCommand(Player.COMMAND_SET_VOLUME) { active ->
        if (volume.isFinite()) {
            active.volume = volume.coerceIn(0f, 1f)
        }
    }

    fun setRepeatMode(mode: PlayerRepeatMode) =
        withControllerCommand(Player.COMMAND_SET_REPEAT_MODE) { active ->
            active.repeatMode = when (mode) {
                PlayerRepeatMode.OFF -> Player.REPEAT_MODE_OFF
                PlayerRepeatMode.ONE -> Player.REPEAT_MODE_ONE
            }
        }

    fun selectTrack(kind: PlayerTrackKind, groupId: String?) =
        withControllerCommand(Player.COMMAND_SET_TRACK_SELECTION_PARAMETERS) { active ->
            if (groupId == null && kind != PlayerTrackKind.SUBTITLE) return@withControllerCommand
            val type = if (kind == PlayerTrackKind.AUDIO) C.TRACK_TYPE_AUDIO else C.TRACK_TYPE_TEXT
            val builder = active.trackSelectionParameters.buildUpon().clearOverridesOfType(type)
                .setTrackTypeDisabled(type, groupId == null)
            if (groupId != null) {
                val group = active.currentTracks.groups.singleOrNull { it.mediaTrackGroup.id == groupId && it.type == type }
                    ?: return@withControllerCommand
                builder.setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0))
            }
            active.trackSelectionParameters = builder.build()
        }

    fun beginSubtitleSelection(): Boolean {
        val active = mutableController.value ?: return false
        val item = active.currentMediaItem ?: return false
        val sequence = item.mediaMetadata.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA) ?: return false
        if (!playerState.value.canAddSubtitle || sequence <= 0) return false
        subtitleTarget = item.mediaId to sequence
        return true
    }

    fun onSubtitleSelected(uri: Uri?) {
        val target = subtitleTarget
        subtitleTarget = null
        if (uri == null || target == null) return
        val active = mutableController.value ?: return
        val generation = connectionGeneration
        val operation = ++subtitleOperationGeneration
        val command = SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY)
        if (!active.isConnected || !active.isSessionCommandAvailable(command)) return
        subtitleMessage = applicationContext.getString(R.string.subtitle_loading)
        subtitleMessageMediaId = target.first
        subtitleMessageSequence = target.second
        refreshState(active)
        val future = active.sendCustomCommand(command, Bundle().apply {
            putString(SubtitleRequestContract.URI, uri.toString())
            putString(SubtitleRequestContract.MEDIA_ID, target.first)
            putLong(SubtitleRequestContract.REQUEST_SEQUENCE, target.second)
        })
        future.addListener({
            if (cleared || operation != subtitleOperationGeneration || generation != connectionGeneration || mutableController.value !== active ||
                active.currentMediaItem?.mediaId != target.first ||
                active.currentMediaItem?.mediaMetadata?.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA) != target.second) return@addListener
            val result = runCatching { future.get() }.getOrNull()
            subtitleMessage = if (result?.resultCode == SessionResult.RESULT_SUCCESS) {
                applicationContext.getString(R.string.subtitle_added)
            } else {
                result?.extras?.getString(SubtitleRequestContract.ERROR_MESSAGE)
                    ?: applicationContext.getString(R.string.subtitle_failed)
            }
            refreshState(active)
        }, mainExecutor)
    }

    /** Covers the short interval before the service's completed checkpoint reaches Room. */
    fun hasObservedCompletion(mediaId: String): Boolean = lastCompletedMediaId == mediaId

    private fun connect(reconnecting: Boolean) {
        if (cleared) return
        reconnectJob?.cancel()
        reconnectJob = null
        val generation = ++connectionGeneration
        mutablePlayerState.value = unavailableState(
            if (reconnecting) {
                PlayerConnectionStatus.RECONNECTING
            } else {
                PlayerConnectionStatus.CONNECTING
            },
        )

        val connectionListener = object : MediaController.Listener {
            override fun onDisconnected(controller: MediaController) {
                handleDisconnected(controller, generation)
            }
        }
        val future = MediaController.Builder(applicationContext, sessionToken)
            .setApplicationLooper(Looper.getMainLooper())
            .setListener(connectionListener)
            .buildAsync()
        controllerFuture = future
        future.addListener(
            {
                if (cleared || generation != connectionGeneration || controllerFuture !== future) {
                    MediaController.releaseFuture(future)
                    return@addListener
                }
                runCatching(future::get)
                    .onSuccess { connectedController ->
                        if (!connectedController.isConnected) {
                            releaseControllerFuture()
                            scheduleReconnect(generation)
                            return@onSuccess
                        }
                        mutableController.value = connectedController
                        reconnectAttempt = 0
                        connectedController.addListener(playerListener)
                        refreshState(connectedController)
                        startPositionTicker(connectedController, generation)
                    }
                    .onFailure {
                        releaseControllerFuture()
                        scheduleReconnect(generation)
                    }
            },
            mainExecutor,
        )
    }

    private fun scheduleReconnect(expectedGeneration: Long) {
        if (cleared || expectedGeneration != connectionGeneration) return
        mutablePlayerState.value = unavailableState(PlayerConnectionStatus.RECONNECTING)
        val reconnectDelayMs = (
            RECONNECT_BASE_DELAY_MS *
                (1L shl reconnectAttempt.coerceAtMost(MAX_RECONNECT_BACKOFF_SHIFT))
            ).coerceAtMost(RECONNECT_MAX_DELAY_MS)
        reconnectAttempt = (reconnectAttempt + 1).coerceAtMost(MAX_RECONNECT_BACKOFF_SHIFT)
        reconnectJob?.cancel()
        reconnectJob = viewModelScope.launch {
            delay(reconnectDelayMs)
            if (!cleared && expectedGeneration == connectionGeneration) {
                connect(reconnecting = true)
            }
        }
    }

    private fun startPositionTicker(
        connectedController: MediaController,
        generation: Long,
    ) {
        positionTickerJob?.cancel()
        positionTickerJob = viewModelScope.launch {
            while (isActive && !cleared && generation == connectionGeneration &&
                mutableController.value === connectedController
            ) {
                if (!connectedController.isConnected) {
                    handleDisconnected(connectedController, generation)
                    break
                }
                refreshState(connectedController)
                delay(POSITION_REFRESH_INTERVAL_MS)
            }
        }
    }

    private fun handleDisconnected(
        disconnectedController: MediaController,
        generation: Long,
    ) {
        if (cleared || generation != connectionGeneration ||
            mutableController.value !== disconnectedController
        ) {
            return
        }
        detachConnectedController(disconnectedController)
        releaseControllerFuture()
        scheduleReconnect(generation)
    }

    private fun refreshState(player: Player) {
        if (cleared || mutableController.value !== player) return
        val currentItem = player.currentMediaItem
        val duration = player.duration.knownTime()
        val position = player.currentPosition.coerceAtLeast(0L)
        val bufferedPosition = player.bufferedPosition.knownTime()
        val metadata = player.mediaMetadata
        val error = player.playerError
        val playbackStatus = when {
            error != null -> PlayerPlaybackStatus.ERROR
            currentItem == null -> PlayerPlaybackStatus.EMPTY
            player.playbackState == Player.STATE_BUFFERING && duration == null && position == 0L ->
                PlayerPlaybackStatus.LOADING

            player.playbackState == Player.STATE_BUFFERING -> PlayerPlaybackStatus.BUFFERING
            player.playbackState == Player.STATE_READY && player.isPlaying ->
                PlayerPlaybackStatus.PLAYING

            player.playbackState == Player.STATE_READY -> PlayerPlaybackStatus.PAUSED
            player.playbackState == Player.STATE_ENDED -> PlayerPlaybackStatus.ENDED
            player.playbackState == Player.STATE_IDLE -> PlayerPlaybackStatus.STOPPED
            else -> PlayerPlaybackStatus.PAUSED
        }
        val currentMediaId = currentItem?.mediaId
        if (currentMediaId != subtitleMessageMediaId ||
            currentItem?.mediaMetadata?.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA) != subtitleMessageSequence) {
            subtitleMessage = null
        }
        if (playbackStatus == PlayerPlaybackStatus.ENDED && currentMediaId != null) {
            lastCompletedMediaId = currentMediaId
        } else if (currentMediaId == lastCompletedMediaId) {
            lastCompletedMediaId = null
        }
        mutablePlayerState.value = PlayerUiState(
            connectionStatus = PlayerConnectionStatus.CONNECTED,
            playbackStatus = playbackStatus,
            mediaId = currentMediaId,
            title = metadata.title.cleanText(),
            supportingText = listOfNotNull(
                metadata.artist.cleanText(),
                metadata.albumTitle.cleanText(),
            ).distinct().joinToString(METADATA_SEPARATOR).ifBlank { null },
            positionMs = duration?.let { position.coerceIn(0L, it) } ?: position,
            durationMs = duration,
            bufferedPositionMs = bufferedPosition,
            playWhenReady = player.playWhenReady,
            playbackSpeed = player.playbackParameters.speed.finiteOrDefault(1f)
                .coerceIn(MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED),
            volume = player.volume.finiteOrDefault(1f).coerceIn(0f, 1f),
            repeatMode = if (player.repeatMode == Player.REPEAT_MODE_ONE) {
                PlayerRepeatMode.ONE
            } else {
                PlayerRepeatMode.OFF
            },
            canPlayPause = currentItem != null &&
                player.isCommandAvailable(Player.COMMAND_PLAY_PAUSE),
            canStop = currentItem != null && player.isCommandAvailable(Player.COMMAND_STOP),
            canSeek = currentItem != null && duration != null &&
                player.isCommandAvailable(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM),
            canSetSpeed = currentItem != null &&
                player.isCommandAvailable(Player.COMMAND_SET_SPEED_AND_PITCH),
            canSetVolume = player.isCommandAvailable(Player.COMMAND_SET_VOLUME),
            canSetRepeat = currentItem != null &&
                player.isCommandAvailable(Player.COMMAND_SET_REPEAT_MODE),
            canRenderVideo = player.isCommandAvailable(Player.COMMAND_SET_VIDEO_SURFACE),
            tracks = player.currentTracks.groups.mapNotNull { group ->
                val kind = when (group.type) {
                    C.TRACK_TYPE_AUDIO -> PlayerTrackKind.AUDIO
                    C.TRACK_TYPE_TEXT -> PlayerTrackKind.SUBTITLE
                    else -> return@mapNotNull null
                }
                if (group.length != 1) return@mapNotNull null
                val format = group.getTrackFormat(0)
                PlayerTrackUiItem(
                    id = group.mediaTrackGroup.id, kind = kind,
                    label = listOfNotNull(format.label, format.language, format.codecs)
                        .filter(String::isNotBlank).distinct().joinToString(" · ").ifBlank { format.id ?: "Track" },
                    selected = group.isTrackSelected(0),
                )
            },
            canSelectTracks = player.isCommandAvailable(Player.COMMAND_SET_TRACK_SELECTION_PARAMETERS),
            canAddSubtitle = currentItem != null && player.playbackState == Player.STATE_READY &&
                player.isSessionCommandAvailable(SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY)),
            subtitleMessage = subtitleMessage,
            errorMessage = error?.message?.takeUnless(String::isBlank),
        )
    }

    private inline fun withControllerCommand(
        command: Int,
        action: (MediaController) -> Unit,
    ) {
        val active = mutableController.value ?: return
        if (!active.isConnected || !active.isCommandAvailable(command)) return
        action(active)
        refreshState(active)
    }

    private fun unavailableState(connectionStatus: PlayerConnectionStatus): PlayerUiState =
        mutablePlayerState.value.copy(
            connectionStatus = connectionStatus,
            canPlayPause = false,
            canStop = false,
            canSeek = false,
            canSetSpeed = false,
            canSetVolume = false,
            canSetRepeat = false,
            canRenderVideo = false,
            canSelectTracks = false,
            canAddSubtitle = false,
        )

    private fun detachConnectedController(controller: MediaController) {
        positionTickerJob?.cancel()
        positionTickerJob = null
        controller.removeListener(playerListener)
        if (mutableController.value === controller) {
            mutableController.value = null
        }
    }

    private fun releaseControllerFuture() {
        val future = controllerFuture ?: return
        controllerFuture = null
        MediaController.releaseFuture(future)
    }

    override fun onCleared() {
        cleared = true
        ++connectionGeneration
        reconnectJob?.cancel()
        reconnectJob = null
        mutableController.value?.let(::detachConnectedController)
        releaseControllerFuture()
    }
}

private fun Long.knownTime(): Long? = takeIf { it != C.TIME_UNSET && it >= 0L }

private fun CharSequence?.cleanText(): String? = this?.toString()?.takeUnless(String::isBlank)

private fun Float.finiteOrDefault(default: Float): Float = if (isFinite()) this else default

private const val POSITION_REFRESH_INTERVAL_MS = 500L
private const val RECONNECT_BASE_DELAY_MS = 1_000L
private const val RECONNECT_MAX_DELAY_MS = 30_000L
private const val MAX_RECONNECT_BACKOFF_SHIFT = 5
private const val MIN_PLAYBACK_SPEED = 0.25f
private const val MAX_PLAYBACK_SPEED = 4f
private const val METADATA_SEPARATOR = " · "
