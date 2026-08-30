// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.Intent
import android.net.Uri
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.player.PlayerHomeScreen
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

@Composable
fun ZivPlayerApp(controller: Player? = null) {
    val context = LocalContext.current
    var pendingMediaUri by rememberSaveable { mutableStateOf<Uri?>(null) }
    var mediaSelectionGeneration by rememberSaveable { mutableLongStateOf(0L) }
    val openMedia = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching {
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            }
            pendingMediaUri = uri
            mediaSelectionGeneration += 1L
        }
    }
    LaunchedEffect(controller, pendingMediaUri, mediaSelectionGeneration) {
        val activeController = controller ?: return@LaunchedEffect
        val uri = pendingMediaUri ?: return@LaunchedEffect
        activeController.setMediaItem(
            MediaItem.Builder()
                .setMediaId(uri.toString())
                .setUri(uri)
                .build(),
        )
        activeController.prepare()
        activeController.awaitCommand(Player.COMMAND_PLAY_PAUSE)
        activeController.play()
        pendingMediaUri = null
    }

    ZivTheme {
        PlayerHomeScreen(
            onOpenMedia = { openMedia.launch(arrayOf("video/*", "audio/*")) },
            videoContent = { PlayerVideoSurface(controller) },
        )
    }
}

private suspend fun Player.awaitCommand(command: Int) {
    while (!isCommandAvailable(command)) {
        suspendCancellableCoroutine { continuation ->
            var completed = false
            lateinit var listener: Player.Listener
            fun resumeWhenAvailable() {
                if (!completed && continuation.isActive && isCommandAvailable(command)) {
                    completed = true
                    removeListener(listener)
                    continuation.resume(Unit)
                }
            }
            listener = object : Player.Listener {
                override fun onAvailableCommandsChanged(availableCommands: Player.Commands) {
                    if (availableCommands.contains(command)) {
                        resumeWhenAvailable()
                    }
                }
            }
            addListener(listener)
            continuation.invokeOnCancellation {
                if (!completed) {
                    completed = true
                    removeListener(listener)
                }
            }
            resumeWhenAvailable()
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
