// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

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
import androidx.compose.runtime.getValue
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
import io.github.joyelliot.zivplayer.data.media.MediaDocumentPersistence
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.player.PlayerHomeScreen
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

@Composable
fun ZivPlayerApp(
    controller: Player? = null,
    pendingPlayback: PendingMediaPlayback? = null,
    persistenceNotice: MediaDocumentPersistence? = null,
    selectionFailed: Boolean = false,
    onDocumentSelected: (OpenMediaDocumentResult) -> Unit = {},
    onPlaybackConsumed: (Long) -> Unit = {},
    onPlaybackFailed: (Long) -> Unit = {},
) {
    val openMedia = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        selection?.let(onDocumentSelected)
    }
    LaunchedEffect(controller, pendingPlayback?.requestId) {
        val activeController = controller ?: return@LaunchedEffect
        val request = pendingPlayback ?: return@LaunchedEffect
        val document = request.document
        val metadata = MediaMetadata.Builder().apply {
            document.metadata.title?.let(::setTitle)
            document.metadata.artist?.let(::setArtist)
            document.metadata.album?.let(::setAlbumTitle)
            document.metadata.artworkLocator?.let { setArtworkUri(it.toUri()) }
        }.build()
        try {
            activeController.setMediaItem(
                MediaItem.Builder()
                    .setMediaId(document.mediaId.value)
                    .setUri(document.sourceUri.toUri())
                    .apply { document.mimeType?.let(::setMimeType) }
                    .setMediaMetadata(metadata)
                    .build(),
            )
            activeController.prepare()
            activeController.awaitCommand(Player.COMMAND_PLAY_PAUSE)
            activeController.play()
            onPlaybackConsumed(request.requestId)
        } catch (failure: kotlinx.coroutines.CancellationException) {
            throw failure
        } catch (_: Exception) {
            onPlaybackFailed(request.requestId)
        }
    }

    ZivTheme {
        PlayerHomeScreen(
            statusMessage = if (selectionFailed) {
                stringResource(R.string.media_open_failed)
            } else {
                persistenceNotice?.toUserMessage()
            },
            onOpenMedia = { openMedia.launch(arrayOf("video/*", "audio/*")) },
            videoContent = { PlayerVideoSurface(controller) },
        )
    }
}

@Composable
private fun MediaDocumentPersistence.toUserMessage(): String? = when (this) {
    MediaDocumentPersistence.PERSISTED -> null
    MediaDocumentPersistence.SESSION_ONLY_PERMISSION_UNAVAILABLE ->
        stringResource(R.string.media_session_only_permission)

    MediaDocumentPersistence.SESSION_ONLY_HISTORY_WRITE_FAILED ->
        stringResource(R.string.media_session_only_history)
}

private suspend fun Player.awaitCommand(command: Int) {
    while (!isCommandAvailable(command)) {
        playerError?.let { throw it }
        suspendCancellableCoroutine { continuation ->
            var completed = false
            lateinit var listener: Player.Listener
            fun resumeWhenReady() {
                if (!completed && continuation.isActive) {
                    val error = playerError
                    if (isCommandAvailable(command) || error != null) {
                        completed = true
                        removeListener(listener)
                        if (error == null) {
                            continuation.resume(Unit)
                        } else {
                            continuation.resumeWithException(error)
                        }
                    }
                }
            }
            listener = object : Player.Listener {
                override fun onAvailableCommandsChanged(availableCommands: Player.Commands) {
                    if (availableCommands.contains(command)) {
                        resumeWhenReady()
                    }
                }

                override fun onPlayerErrorChanged(error: PlaybackException?) = resumeWhenReady()
            }
            addListener(listener)
            continuation.invokeOnCancellation {
                if (!completed) {
                    completed = true
                    removeListener(listener)
                }
            }
            resumeWhenReady()
        }
    }
}

@Composable
private fun PlayerVideoSurface(player: Player?) {
    val context = LocalContext.current
    val surfaceView = remember(context) { SurfaceView(context) }
    AndroidView(
        factory = { surfaceView },
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(16f / 9f)
            .background(Color.Black),
    )

    DisposableEffect(player, surfaceView) {
        val holder = surfaceView.holder
        if (player == null) {
            onDispose { }
        } else {
            var attachedSurface: Surface? = null
            val callback = object : SurfaceHolder.Callback {
                override fun surfaceCreated(surfaceHolder: SurfaceHolder) {
                    val surface = surfaceHolder.surface
                    if (surface.isValid) {
                        attachedSurface = surface
                        player.setVideoSurface(surface)
                    }
                }

                override fun surfaceChanged(
                    surfaceHolder: SurfaceHolder,
                    format: Int,
                    width: Int,
                    height: Int,
                ) = Unit

                override fun surfaceDestroyed(surfaceHolder: SurfaceHolder) {
                    attachedSurface?.let(player::clearVideoSurface)
                    attachedSurface = null
                }
            }
            holder.addCallback(callback)
            if (holder.surface.isValid) {
                attachedSurface = holder.surface
                player.setVideoSurface(holder.surface)
            }
            onDispose {
                holder.removeCallback(callback)
                attachedSurface?.let(player::clearVideoSurface)
                attachedSurface = null
            }
        }
    }
}
