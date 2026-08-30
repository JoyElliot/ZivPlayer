// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.net.toUri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.player.PlayerHomeScreen
import io.github.joyelliot.zivplayer.feature.player.PlayerRepeatMode
import io.github.joyelliot.zivplayer.feature.player.PlayerUiState
import io.github.joyelliot.zivplayer.feature.player.RecentMediaUiItem
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeout
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

@Composable
fun ZivPlayerApp(
    controller: MediaController? = null,
    playerState: PlayerUiState = PlayerUiState(),
    pendingPlayback: PendingMediaPlayback? = null,
    recentMedia: List<RecentMediaUiItem> = emptyList(),
    selectionNotice: MediaSelectionNotice? = null,
    historyUnavailable: Boolean = false,
    onDocumentSelected: (OpenMediaDocumentResult) -> Unit = {},
    onPlaybackConsumed: (Long) -> Unit = {},
    onPlaybackFailed: (Long) -> Unit = {},
    isPlaybackPending: (Long) -> Boolean = { true },
    onPlayPause: () -> Unit = {},
    onStop: () -> Unit = {},
    onSeekTo: (Long) -> Unit = {},
    onPlaybackSpeedChange: (Float) -> Unit = {},
    onVolumeChange: (Float) -> Unit = {},
    onRepeatModeChange: (PlayerRepeatMode) -> Unit = {},
    onOpenRecent: (String) -> Unit = {},
    onForgetRecent: (String) -> Unit = {},
) {
    val openMedia = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        selection?.let(onDocumentSelected)
    }
    LaunchedEffect(controller, pendingPlayback?.requestId) {
        val activeController = controller ?: return@LaunchedEffect
        val request = pendingPlayback ?: return@LaunchedEffect
        if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
        val document = request.document
        val metadata = MediaMetadata.Builder().apply {
            setExtras(
                Bundle().apply {
                    putString(PlaybackRequestMetadata.TOKEN_EXTRA, request.dispatchToken)
                    putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, request.requestId)
                },
            )
            document.metadata.title?.let(::setTitle)
            document.metadata.artist?.let(::setArtist)
            document.metadata.album?.let(::setAlbumTitle)
            document.metadata.artworkLocator?.let { setArtworkUri(it.toUri()) }
        }.build()
        try {
            if (!activeController.isConnected) {
                throw PlaybackControllerDisconnectedException()
            }
            val currentItem = activeController.currentMediaItem
            val previousError = activeController.playerError
            val requestAlreadyApplied = previousError == null &&
                activeController.playbackState != Player.STATE_ENDED &&
                currentItem?.mediaId == document.mediaId.value &&
                currentItem.mediaMetadata.extras
                    ?.getString(PlaybackRequestMetadata.TOKEN_EXTRA) == request.dispatchToken
            if (!requestAlreadyApplied) {
                val sameActiveMedia = currentItem?.mediaId == document.mediaId.value
                val startPositionMs = when {
                    sameActiveMedia && (
                        activeController.playbackState == Player.STATE_IDLE ||
                            activeController.playbackState == Player.STATE_ENDED
                        ) -> 0L
                    sameActiveMedia && activeController.playerError == null -> maxOf(
                        request.startPositionMs,
                        activeController.currentPosition.coerceAtLeast(0L),
                    )

                    else -> request.startPositionMs
                }
                activeController.awaitCommand(Player.COMMAND_SET_MEDIA_ITEM)
                if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
                activeController.setMediaItem(
                    MediaItem.Builder()
                        .setMediaId(document.mediaId.value)
                        .setUri(document.sourceUri.toUri())
                        .apply { document.mimeType?.let(::setMimeType) }
                        .setMediaMetadata(metadata)
                        .build(),
                    startPositionMs,
                )
            }
            activeController.awaitRequestInstalled(request.dispatchToken, previousError)
            if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
            activeController.awaitCommand(Player.COMMAND_PREPARE)
            if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
            activeController.prepare()
            activeController.awaitCommand(Player.COMMAND_PLAY_PAUSE)
            if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
            activeController.play()
            activeController.awaitPlaybackReady(request.dispatchToken)
            if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
            onPlaybackConsumed(request.requestId)
        } catch (failure: kotlinx.coroutines.CancellationException) {
            throw failure
        } catch (_: PlaybackControllerDisconnectedException) {
            // Keep the request pending; a fresh controller will replay it after reconnect.
        } catch (_: Exception) {
            onPlaybackFailed(request.requestId)
        }
    }

    ZivTheme {
        PlayerHomeScreen(
            state = playerState,
            recentMedia = recentMedia,
            statusMessage = selectionNotice?.toUserMessage()
                ?: if (historyUnavailable) {
                    stringResource(R.string.media_history_unavailable)
                } else {
                    null
                },
            onOpenMedia = { openMedia.launch(arrayOf("video/*", "audio/*")) },
            onPlayPause = onPlayPause,
            onStop = onStop,
            onSeekTo = onSeekTo,
            onPlaybackSpeedChange = onPlaybackSpeedChange,
            onVolumeChange = onVolumeChange,
            onRepeatModeChange = onRepeatModeChange,
            onOpenRecent = onOpenRecent,
            onForgetRecent = onForgetRecent,
            videoContent = {
                PlayerVideoSurface(
                    player = controller,
                    canRenderVideo = playerState.canRenderVideo,
                )
            },
        )
    }
}

@Composable
private fun MediaSelectionNotice.toUserMessage(): String = when (this) {
    MediaSelectionNotice.SESSION_ONLY_PERMISSION ->
        stringResource(R.string.media_session_only_permission)

    MediaSelectionNotice.SESSION_ONLY_HISTORY ->
        stringResource(R.string.media_session_only_history)

    MediaSelectionNotice.OPEN_FAILED -> stringResource(R.string.media_open_failed)
    MediaSelectionNotice.RECENT_ACCESS_LOST ->
        stringResource(R.string.media_recent_access_lost)

    MediaSelectionNotice.HISTORY_UNAVAILABLE ->
        stringResource(R.string.media_history_unavailable)

    MediaSelectionNotice.HISTORY_FORGOTTEN ->
        stringResource(R.string.media_history_forgotten)

    MediaSelectionNotice.HISTORY_FORGOTTEN_WITH_ORPHANED_GRANT ->
        stringResource(R.string.media_history_forgotten_orphaned_grant)

    MediaSelectionNotice.HISTORY_FORGET_FAILED ->
        stringResource(R.string.media_history_forget_failed)
}

private suspend fun MediaController.awaitCommand(command: Int) {
    try {
        withTimeout(COMMAND_WAIT_TIMEOUT_MS) {
            awaitCommandWithoutTimeout(command)
        }
    } catch (_: TimeoutCancellationException) {
        throw IllegalStateException("Timed out while awaiting playback command $command.")
    }
}

private suspend fun MediaController.awaitCommandWithoutTimeout(command: Int) {
    while (true) {
        if (!isConnected) {
            throw PlaybackControllerDisconnectedException()
        }
        if (isCommandAvailable(command)) {
            return
        }
        playerError?.let { throw it }
        suspendCancellableCoroutine { continuation ->
            var completed = false
            lateinit var listener: Player.Listener
            fun resumeWhenReady() {
                if (!completed && continuation.isActive) {
                    val error = playerError
                    val disconnected = !isConnected
                    val commandAvailable = !disconnected && isCommandAvailable(command)
                    if (disconnected || commandAvailable || error != null) {
                        completed = true
                        removeListenerOnApplicationLooper(listener)
                        when {
                            disconnected -> continuation.resumeWithException(
                                PlaybackControllerDisconnectedException(),
                            )

                            commandAvailable -> continuation.resume(Unit)
                            else -> continuation.resumeWithException(checkNotNull(error))
                        }
                    }
                }
            }
            listener = object : Player.Listener {
                override fun onAvailableCommandsChanged(availableCommands: Player.Commands) {
                    resumeWhenReady()
                }

                override fun onPlayerErrorChanged(error: PlaybackException?) = resumeWhenReady()

                override fun onEvents(player: Player, events: Player.Events) = resumeWhenReady()
            }
            addListener(listener)
            continuation.invokeOnCancellation {
                if (!completed) {
                    completed = true
                    removeListenerOnApplicationLooper(listener)
                }
            }
            resumeWhenReady()
        }
    }
}

private suspend fun MediaController.awaitPlaybackReady(dispatchToken: String) {
    try {
        withTimeout(COMMAND_WAIT_TIMEOUT_MS) {
            while (true) {
                if (!isConnected) {
                    throw PlaybackControllerDisconnectedException()
                }
                playerError?.let { throw it }
                if (isPlaybackReady(dispatchToken)) {
                    return@withTimeout
                }
                suspendCancellableCoroutine { continuation ->
                    var completed = false
                    lateinit var listener: Player.Listener
                    fun resumeWhenSettled() {
                        if (!completed && continuation.isActive) {
                            val error = playerError
                            val disconnected = !isConnected
                            val playbackReady = !disconnected && error == null &&
                                isPlaybackReady(dispatchToken)
                            if (disconnected || error != null || playbackReady) {
                                completed = true
                                removeListenerOnApplicationLooper(listener)
                                when {
                                    disconnected -> continuation.resumeWithException(
                                        PlaybackControllerDisconnectedException(),
                                    )

                                    error != null -> continuation.resumeWithException(error)
                                    else -> continuation.resume(Unit)
                                }
                            }
                        }
                    }
                    listener = object : Player.Listener {
                        override fun onEvents(player: Player, events: Player.Events) {
                            resumeWhenSettled()
                        }

                        override fun onPlayerErrorChanged(error: PlaybackException?) {
                            resumeWhenSettled()
                        }
                    }
                    addListener(listener)
                    continuation.invokeOnCancellation {
                        if (!completed) {
                            completed = true
                            removeListenerOnApplicationLooper(listener)
                        }
                    }
                    resumeWhenSettled()
                }
            }
        }
    } catch (_: TimeoutCancellationException) {
        throw IllegalStateException("Timed out while preparing the selected media.")
    }
}

private suspend fun MediaController.awaitRequestInstalled(
    dispatchToken: String,
    ignoredPreviousError: PlaybackException?,
) {
    try {
        withTimeout(COMMAND_WAIT_TIMEOUT_MS) {
            while (true) {
                if (!isConnected) {
                    throw PlaybackControllerDisconnectedException()
                }
                playerError?.takeUnless { it === ignoredPreviousError }?.let { throw it }
                if (isRequestInstalled(dispatchToken)) {
                    return@withTimeout
                }
                suspendCancellableCoroutine { continuation ->
                    var completed = false
                    lateinit var listener: Player.Listener
                    fun resumeWhenInstalled() {
                        if (!completed && continuation.isActive) {
                            val disconnected = !isConnected
                            val error = playerError?.takeUnless { it === ignoredPreviousError }
                            val requestInstalled = !disconnected &&
                                isRequestInstalled(dispatchToken)
                            if (disconnected || error != null || requestInstalled) {
                                completed = true
                                removeListenerOnApplicationLooper(listener)
                                when {
                                    disconnected -> continuation.resumeWithException(
                                        PlaybackControllerDisconnectedException(),
                                    )

                                    error != null -> continuation.resumeWithException(error)
                                    else -> continuation.resume(Unit)
                                }
                            }
                        }
                    }
                    listener = object : Player.Listener {
                        override fun onEvents(player: Player, events: Player.Events) {
                            resumeWhenInstalled()
                        }

                        override fun onPlayerErrorChanged(error: PlaybackException?) {
                            resumeWhenInstalled()
                        }
                    }
                    addListener(listener)
                    continuation.invokeOnCancellation {
                        if (!completed) {
                            completed = true
                            removeListenerOnApplicationLooper(listener)
                        }
                    }
                    resumeWhenInstalled()
                }
            }
        }
    } catch (_: TimeoutCancellationException) {
        throw IllegalStateException("Timed out while installing the selected media.")
    }
}

private fun MediaController.isPlaybackReady(dispatchToken: String): Boolean {
    val tokenMatches = currentMediaItem?.mediaMetadata?.extras
        ?.getString(PlaybackRequestMetadata.TOKEN_EXTRA) == dispatchToken
    return tokenMatches &&
        (isPlaying || playbackState == Player.STATE_ENDED)
}

private fun MediaController.isRequestInstalled(dispatchToken: String): Boolean =
    playerError == null && currentMediaItem?.mediaMetadata?.extras
        ?.getString(PlaybackRequestMetadata.TOKEN_EXTRA) == dispatchToken

private fun MediaController.removeListenerOnApplicationLooper(listener: Player.Listener) {
    if (Looper.myLooper() == applicationLooper) {
        removeListener(listener)
    } else {
        Handler(applicationLooper).post { removeListener(listener) }
    }
}

@Composable
private fun PlayerVideoSurface(
    player: MediaController?,
    canRenderVideo: Boolean,
) {
    val context = LocalContext.current
    val surfaceView = remember(context) { SurfaceView(context) }
    AndroidView(
        factory = { surfaceView },
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(16f / 9f)
            .background(Color.Black),
    )

    DisposableEffect(player, surfaceView, canRenderVideo) {
        val holder = surfaceView.holder
        if (player == null || !canRenderVideo || !player.canSetVideoSurface()) {
            onDispose { }
        } else {
            var attachedSurface: Surface? = null
            fun attachSurface(surface: Surface) {
                if (surface.isValid && player.canSetVideoSurface() && attachedSurface != surface) {
                    attachedSurface = surface
                    player.setVideoSurface(surface)
                }
            }
            val callback = object : SurfaceHolder.Callback {
                override fun surfaceCreated(surfaceHolder: SurfaceHolder) {
                    attachSurface(surfaceHolder.surface)
                }

                override fun surfaceChanged(
                    surfaceHolder: SurfaceHolder,
                    format: Int,
                    width: Int,
                    height: Int,
                ) = Unit

                override fun surfaceDestroyed(surfaceHolder: SurfaceHolder) {
                    attachedSurface?.let { surface ->
                        if (player.canSetVideoSurface()) {
                            player.clearVideoSurface(surface)
                        }
                    }
                    attachedSurface = null
                }
            }
            holder.addCallback(callback)
            attachSurface(holder.surface)
            onDispose {
                holder.removeCallback(callback)
                attachedSurface?.let { surface ->
                    if (player.canSetVideoSurface()) {
                        player.clearVideoSurface(surface)
                    }
                }
                attachedSurface = null
            }
        }
    }
}

private fun MediaController.canSetVideoSurface(): Boolean =
    isConnected && isCommandAvailable(Player.COMMAND_SET_VIDEO_SURFACE)

private class PlaybackControllerDisconnectedException : IllegalStateException(
    "Playback service disconnected while handling a media request.",
)

private const val COMMAND_WAIT_TIMEOUT_MS = 20_000L
