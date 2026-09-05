// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.net.Uri
import android.util.Log
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.movableContentOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.dp
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
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.designsystem.ZivAppearance
import io.github.joyelliot.zivplayer.designsystem.ZivPrimaryButton
import io.github.joyelliot.zivplayer.feature.library.LibraryScreen
import io.github.joyelliot.zivplayer.feature.settings.SettingsScreen
import io.github.joyelliot.zivplayer.feature.settings.DiagnosticsScreen
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import io.github.joyelliot.zivplayer.feature.player.PlayerHomeScreen
import io.github.joyelliot.zivplayer.feature.player.PlayerRepeatMode
import io.github.joyelliot.zivplayer.feature.player.PlayerUiState
import io.github.joyelliot.zivplayer.feature.player.PlayerTrackKind
import io.github.joyelliot.zivplayer.feature.player.RecentMediaUiItem
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.VideoSurfaceRequestContract
import io.github.joyelliot.zivplayer.platform.playback.VideoSurfaceRequests
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
    onSelectTrack: (PlayerTrackKind, String?) -> Unit = { _, _ -> },
    onBeginSubtitleSelection: () -> Boolean = { false },
    onSubtitleSelected: (Uri?) -> Unit = {},
    onOpenRecent: (String) -> Unit = {},
    onForgetRecent: (String) -> Unit = {},
    library: LibraryViewModel? = null,
    settings: SettingsViewModel? = null,
    fullscreen: Boolean = false,
    pictureInPicture: Boolean = false,
    onFullscreenChange: (Boolean) -> Unit = {},
    onEnterPictureInPicture: () -> Unit = {},
    onPlayerVisibilityChange: (Boolean) -> Unit = {},
    diagnostics: PlaybackDiagnostics? = null,
    diagnosticsMessage: String? = null,
    configurationMessage: String? = null,
    onDiagnosticsVisibilityChange: (Boolean) -> Unit = {},
    onExportDiagnostics: () -> Unit = {},
) {
    var destination by rememberSaveable { mutableStateOf(AppDestination.PLAYER) }
    var showDiagnostics by rememberSaveable { mutableStateOf(false) }
    var importKind by rememberSaveable { mutableStateOf(PlaybackResourceKind.FONT) }
    val preferences = settings?.preferences?.value ?: PlayerPreferences()
    val expanded = fullscreen || pictureInPicture
    val renderState = rememberUpdatedState(Triple(controller, playerState.canRenderVideo, expanded))
    val videoContent = remember {
        movableContentOf {
            val (player, canRender, fill) = renderState.value
            PlayerVideoSurface(player, canRender, if (fill) Modifier.fillMaxSize()
                else Modifier.fillMaxWidth().aspectRatio(16f / 9f))
        }
    }
    LaunchedEffect(destination, expanded) { onPlayerVisibilityChange(destination == AppDestination.PLAYER || expanded) }
    LaunchedEffect(destination, showDiagnostics, expanded) {
        onDiagnosticsVisibilityChange(destination == AppDestination.SETTINGS && showDiagnostics && !expanded)
    }
    DisposableEffect(Unit) { onDispose { onDiagnosticsVisibilityChange(false) } }
    BackHandler(enabled = fullscreen || destination != AppDestination.PLAYER || showDiagnostics) {
        when {
            fullscreen -> onFullscreenChange(false)
            showDiagnostics -> showDiagnostics = false
            else -> destination = AppDestination.PLAYER
        }
    }
    val openFolder = rememberLauncherForActivityResult(OpenMediaFolder()) { selection ->
        selection?.let { library?.addFolder(it) }
    }
    val importResource = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        selection?.let { settings?.importResource(it.uri, importKind) }
    }
    val openMedia = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        selection?.let(onDocumentSelected)
    }
    val openSubtitle = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        onSubtitleSelected(selection?.uri)
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

    ZivTheme(appearance = ZivAppearance.valueOf(preferences.appearance.name)) {
        if (expanded) {
            Box(Modifier.fillMaxSize().background(Color.Black)) {
                videoContent()
                if (!pictureInPicture) Row(Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ZivPrimaryButton("−${preferences.seekStepSeconds}s", { onSeekTo((playerState.positionMs - preferences.seekStepSeconds * 1000).coerceAtLeast(0)) }, enabled = playerState.canSeek)
                    ZivPrimaryButton(stringResource(R.string.app_play_pause), onPlayPause, enabled = playerState.canPlayPause)
                    ZivPrimaryButton("+${preferences.seekStepSeconds}s", { onSeekTo(playerState.positionMs + preferences.seekStepSeconds * 1000) }, enabled = playerState.canSeek)
                    ZivPrimaryButton(stringResource(R.string.app_exit_fullscreen), { onFullscreenChange(false) })
                }
            }
        } else Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) { when (destination) {
            AppDestination.PLAYER ->
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
            onSelectTrack = onSelectTrack,
            onOpenSubtitle = {
                if (onBeginSubtitleSelection()) {
                    // SAF providers frequently classify ASS/SRT as generic binary documents.
                    openSubtitle.launch(arrayOf("*/*"))
                }
            },
            onOpenRecent = onOpenRecent,
            onForgetRecent = onForgetRecent,
            videoContent = videoContent,
            extraControls = {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ZivPrimaryButton(stringResource(R.string.app_fullscreen), { onFullscreenChange(true) }, Modifier.weight(1f), enabled = playerState.canRenderVideo && playerState.hasVideo)
                    ZivPrimaryButton(stringResource(R.string.app_pip), onEnterPictureInPicture, Modifier.weight(1f), enabled = playerState.canRenderVideo && playerState.hasVideo)
                }
            },
        )
            AppDestination.LIBRARY -> LibraryScreen(
                folders = library?.folders?.value.orEmpty(), media = library?.media?.value.orEmpty(),
                scanningFolderId = library?.progress?.value?.folderId, scannedCount = library?.progress?.value?.mediaFound ?: 0,
                message = library?.message?.value, onAddFolder = { openFolder.launch(Unit) },
                onScan = { library?.scan(it) }, onCancelScan = { library?.cancelScan() },
                onRemoveFolder = { library?.removeFolder(it) }, onFavorite = { library?.setFavorite(it) }, onHidden = { library?.setHidden(it) },
                onOpen = { item -> destination = AppDestination.PLAYER; onDocumentSelected(OpenMediaDocumentResult(Uri.parse(item.sourceUri.value), 0)) },
            )
            AppDestination.SETTINGS -> if (showDiagnostics) DiagnosticsScreen(
                snapshot = diagnostics, device = "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL} · Android ${android.os.Build.VERSION.RELEASE}",
                message = diagnosticsMessage, onBack = { showDiagnostics = false }, onExport = onExportDiagnostics,
            ) else SettingsScreen(
                value = preferences, resources = settings?.resources?.value.orEmpty(), message = configurationMessage ?: settings?.message?.value,
                busy = settings?.busy?.value ?: false, onUpdate = { settings?.update(it) },
                onImport = { kind -> importKind = kind; importResource.launch(arrayOf("*/*")) }, onDelete = { settings?.deleteResource(it) },
                onDiagnostics = { showDiagnostics = true },
            )
            } }
            Row(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppDestination.entries.forEach { item ->
                    ZivPrimaryButton(stringResource(when (item) {
                        AppDestination.PLAYER -> R.string.app_player
                        AppDestination.LIBRARY -> R.string.app_library
                        AppDestination.SETTINGS -> R.string.app_settings
                    }), { destination = item }, Modifier.weight(1f), enabled = destination != item)
                }
            }
        }
    }
}

private enum class AppDestination { PLAYER, LIBRARY, SETTINGS }

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
    modifier: Modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
) {
    val context = LocalContext.current
    val surfaceView = remember(context) { SurfaceView(context) }
    AndroidView(
        factory = { surfaceView },
        modifier = modifier.background(Color.Black),
    )

    DisposableEffect(player, surfaceView, canRenderVideo) {
        val holder = surfaceView.holder
        if (player == null || !canRenderVideo || !player.canSetVideoSurface()) {
            onDispose { }
        } else {
            var attachedSurface: Surface? = null
            var surfaceToken = 0L
            fun resizeSurface(width: Int, height: Int) {
                if (width <= 0 || height <= 0 || !VideoSurfaceRequests.isCurrent(surfaceToken) || !player.isConnected) return
                val command = SessionCommand(VideoSurfaceRequestContract.ACTION_RESIZE, Bundle.EMPTY)
                if (!player.isSessionCommandAvailable(command)) return
                val future = player.sendCustomCommand(command, Bundle().apply {
                    putLong(VideoSurfaceRequestContract.TOKEN, surfaceToken)
                    putInt(VideoSurfaceRequestContract.WIDTH, width)
                    putInt(VideoSurfaceRequestContract.HEIGHT, height)
                })
                future.addListener({
                    val result = runCatching { future.get() }
                    if (result.getOrNull()?.resultCode != SessionResult.RESULT_SUCCESS) {
                        Log.w("ZivVideoSurface", "Surface resize was not accepted.", result.exceptionOrNull())
                    }
                }, java.util.concurrent.Executor { it.run() })
            }
            fun attachSurface(surface: Surface) {
                if (surface.isValid && player.canSetVideoSurface() && attachedSurface != surface) {
                    attachedSurface = surface
                    surfaceToken = VideoSurfaceRequests.claim()
                    player.setVideoSurface(surface)
                    resizeSurface(holder.surfaceFrame.width(), holder.surfaceFrame.height())
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
                ) {
                    if (surfaceHolder.surface == attachedSurface) resizeSurface(width, height)
                }

                override fun surfaceDestroyed(surfaceHolder: SurfaceHolder) {
                    if (VideoSurfaceRequests.release(surfaceToken)) {
                        attachedSurface?.let { surface ->
                            if (player.canSetVideoSurface()) {
                                player.clearVideoSurface(surface)
                            }
                        }
                    }
                    attachedSurface = null
                }
            }
            holder.addCallback(callback)
            attachSurface(holder.surface)
            onDispose {
                holder.removeCallback(callback)
                if (VideoSurfaceRequests.release(surfaceToken)) {
                    attachedSurface?.let { surface ->
                        if (player.canSetVideoSurface()) {
                            player.clearVideoSurface(surface)
                        }
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
