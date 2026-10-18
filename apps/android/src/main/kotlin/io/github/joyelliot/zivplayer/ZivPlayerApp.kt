// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.movableContentOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.layout
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.platform.LocalFocusManager
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
import io.github.joyelliot.zivplayer.designsystem.ZivAppearance
import io.github.joyelliot.zivplayer.designsystem.*
import io.github.joyelliot.zivplayer.feature.library.LibraryScreen
import io.github.joyelliot.zivplayer.feature.library.LibraryMediaKind
import io.github.joyelliot.zivplayer.feature.settings.SettingsDetailScreen
import io.github.joyelliot.zivplayer.feature.settings.SettingsPage
import io.github.joyelliot.zivplayer.feature.settings.SettingsScreen
import io.github.joyelliot.zivplayer.feature.settings.DiagnosticsScreen
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import io.github.joyelliot.zivplayer.core.model.VideoFit
import io.github.joyelliot.zivplayer.feature.player.FullscreenPlayerScreen
import io.github.joyelliot.zivplayer.feature.player.PlayerQueuePanel
import io.github.joyelliot.zivplayer.feature.player.PlayerQuickOption
import io.github.joyelliot.zivplayer.feature.player.MiniPlayer
import io.github.joyelliot.zivplayer.feature.player.PlaybackQueueScreen
import io.github.joyelliot.zivplayer.feature.player.PlayerRepeatMode
import io.github.joyelliot.zivplayer.feature.player.PlayerUiState
import io.github.joyelliot.zivplayer.feature.player.PlayerTrackKind
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
    onLibraryPlaylist: (List<LibraryMedia>, Int) -> Unit = { _, _ -> },
    onSelectQueueItem: (Int) -> Unit = {},
    onPrevious: () -> Unit = {},
    onNext: () -> Unit = {},
    onPlaybackConsumed: (Long) -> Unit = {},
    onPlaybackFailed: (Long) -> Unit = {},
    isPlaybackPending: (Long) -> Boolean = { true },
    onPlayPause: () -> Unit = {},
    onStop: () -> Unit = {},
    onSeekTo: (Long) -> Unit = {},
    onSeekPreviewTo: (Long) -> Unit = {},
    onSeekPreviewFinished: () -> Unit = {},
    onPlaybackSpeedChange: (Float) -> Unit = {},
    onBeginTemporarySpeed: () -> Long? = { null },
    onTemporarySpeedChange: (Long, Float) -> Unit = { _, _ -> },
    onEndTemporarySpeed: (Long) -> Unit = {},
    interactionEnabled: Boolean = true,
    onVolumeChange: (Float) -> Unit = {},
    brightness: Float? = null,
    onBrightnessChange: (Float) -> Unit = {},
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
    pictureInPictureTransition: Boolean = false,
    onVideoBounds: (android.graphics.Rect) -> Unit = {},
    onFullscreenChange: (Boolean) -> Unit = {},
    onEnterPictureInPicture: () -> Unit = {},
    onPlayerVisibilityChange: (Boolean) -> Unit = {},
    diagnostics: PlaybackDiagnostics? = null,
    diagnosticsMessage: String? = null,
    configurationMessage: String? = null,
    onDiagnosticsVisibilityChange: (Boolean) -> Unit = {},
    onExportDiagnostics: () -> Unit = {},
) {
    var playerOpen by rememberSaveable { mutableStateOf(false) }
    val presentationScope = rememberCoroutineScope()
    val presentation = remember { PlayerPresentationState(presentationScope, playerOpen) }
    var miniFrame by remember { mutableStateOf<VideoFrame?>(null) }
    val rotation by animateFloatAsState(if (fullscreen && !pictureInPicture && !pictureInPictureTransition) 90f else 0f,
        spring(dampingRatio = 1f, stiffness = 360f), label = "player rotation")
    val homePages = rememberHomePagerState()
    val destination = if (playerOpen) AppDestination.PLAYER else homeDestinations[homePages.selectedPage]
    var playerReturnDestination by rememberSaveable { mutableStateOf(AppDestination.VIDEO) }
    val settingsPages = rememberZivPageStackState()
    val showDiagnostics = settingsPages.currentRoute == SettingsPage.DIAGNOSTICS.name
    var showQueue by remember { mutableStateOf(false) }
    var importKind by rememberSaveable { mutableStateOf(PlaybackResourceKind.FONT) }
    val preferences = settings?.preferences?.value ?: PlayerPreferences()
    val navigationStyle = ZivNavigationStyle.valueOf(preferences.navigationStyle.name)
    val expanded = playerOpen || presentation.progress > 0f || pictureInPicture
    val pageState = rememberSaveableStateHolder()
    val libraryBackHandlers = remember { mutableStateMapOf<AppDestination, (() -> Unit)?>() }
    val libraryBack = libraryBackHandlers[destination]
    val focusManager = LocalFocusManager.current
    fun selectHome(page: AppDestination) {
        homePages.navigate(homeDestinations.indexOf(page))
    }
    fun openPlayerFrom(page: AppDestination) {
        if (!playerOpen) playerReturnDestination = page
        homePages.snapToPage(homeDestinations.indexOf(playerReturnDestination))
        playerOpen = true
        presentation.animateTo(true)
    }
    fun openPlayer() = openPlayerFrom(homeDestinations[homePages.selectedPage])
    fun leavePlayer() {
        onFullscreenChange(false)
        showQueue = false
        homePages.snapToPage(homeDestinations.indexOf(playerReturnDestination))
        playerOpen = false
        presentation.animateTo(false)
    }
    LaunchedEffect(expanded) {
        if (expanded) {
            focusManager.clearFocus(force = true)
            homePages.snapToPage(if (playerOpen) homeDestinations.indexOf(playerReturnDestination) else homePages.selectedPage)
        }
    }
    LaunchedEffect(homePages.selectedPage) {
        focusManager.clearFocus()
    }
    LaunchedEffect(destination, expanded) { onPlayerVisibilityChange(destination == AppDestination.PLAYER || expanded) }
    LaunchedEffect(destination, showDiagnostics, expanded, homePages.pager.isScrollInProgress, settingsPages.isTransitionRunning) {
        onDiagnosticsVisibilityChange(destination == AppDestination.SETTINGS && showDiagnostics && !expanded && !homePages.pager.isScrollInProgress && !settingsPages.isTransitionRunning)
    }
    DisposableEffect(Unit) { onDispose { onDiagnosticsVisibilityChange(false) } }
    BackHandler(enabled = (expanded || !settingsPages.canPop) && (fullscreen || destination != AppDestination.VIDEO || libraryBack != null)) {
        when {
            expanded -> leavePlayer()
            destination in listOf(AppDestination.VIDEO, AppDestination.AUDIO) && libraryBack != null -> libraryBack?.invoke()
            destination != AppDestination.VIDEO -> selectHome(AppDestination.VIDEO)
        }
    }
    ZivPredictiveBackHandler(settingsPages, enabled = !expanded)
    val openFolder = rememberLauncherForActivityResult(OpenMediaFolder()) { selection ->
        selection?.let { library?.addFolder(it) }
    }
    val importResource = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        selection?.let { settings?.importResource(it.uri, importKind) }
    }
    val openMedia = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        selection?.let { openPlayer(); onDocumentSelected(it) }
    }
    val openSubtitle = rememberLauncherForActivityResult(OpenMediaDocument()) { selection ->
        onSubtitleSelected(selection?.uri)
    }
    val launchSubtitle: () -> Unit = {
        if (onBeginSubtitleSelection()) openSubtitle.launch(arrayOf("*/*"))
    }
    val videoOptions = if (settings != null) VideoFit.entries.map { fit ->
        PlayerQuickOption(fit.name, stringResource(when (fit) {
            VideoFit.FIT -> R.string.video_fit_inside
            VideoFit.FILL -> R.string.video_fit_crop
            VideoFit.STRETCH -> R.string.video_fit_stretch
        }), preferences.options.videoFit == fit)
    } else emptyList()
    val selectVideoFit: (String) -> Unit = { id ->
        VideoFit.entries.firstOrNull { it.name == id }?.let { fit ->
            settings?.update { current -> current.copy(options = current.options.copy(videoFit = fit)) }
        }
    }
    LaunchedEffect(controller, pendingPlayback?.requestId) {
        val activeController = controller ?: return@LaunchedEffect
        val request = pendingPlayback ?: return@LaunchedEffect
        if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
        val document = request.document
        val mediaItems = request.documents.map { queuedDocument ->
        val metadata = MediaMetadata.Builder().apply {
            setExtras(
                Bundle().apply {
                    putString(PlaybackRequestMetadata.TOKEN_EXTRA, request.dispatchToken)
                    putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, request.requestId)
                },
            )
            queuedDocument.metadata.title?.let(::setTitle)
            queuedDocument.metadata.artist?.let(::setArtist)
            queuedDocument.metadata.album?.let(::setAlbumTitle)
            queuedDocument.metadata.artworkLocator?.let { setArtworkUri(it.toUri()) }
        }.build()
        MediaItem.Builder().setMediaId(queuedDocument.mediaId.value).setUri(queuedDocument.sourceUri.toUri())
            .apply { queuedDocument.mimeType?.let(::setMimeType) }.setMediaMetadata(metadata).build()
        }
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
                activeController.awaitCommand(Player.COMMAND_CHANGE_MEDIA_ITEMS)
                if (!isPlaybackPending(request.requestId)) return@LaunchedEffect
                activeController.setMediaItems(mediaItems, request.startIndex, startPositionMs)
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
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val window = IntSize(constraints.maxWidth, constraints.maxHeight)
            val progress = if (pictureInPicture) 1f else presentation.progress
            val travel = window.height * 0.65f
            val anchor = miniFrame ?: VideoFrame(window.width * 0.15f, window.height * 0.85f, 56f, 36f)
            val frame = presentationFrame(window.width.toFloat(), window.height.toFloat(), playerState.videoAspectRatio,
                anchor, progress, if (pictureInPicture) 0f else rotation,
                fill = preferences.options.videoFit == VideoFit.FILL && !pictureInPicture,
                stretch = preferences.options.videoFit == VideoFit.STRETCH && !pictureInPicture)
            fun settlePresentation(open: Boolean) {
                if (open && !playerOpen) playerReturnDestination = homeDestinations[homePages.selectedPage]
                playerOpen = open
                if (!open) { onFullscreenChange(false); showQueue = false }
            }
            Box(Modifier.fillMaxSize()
                .graphicsLayer { alpha = 1f - progress }
                .then(if (expanded) Modifier.clearAndSetSemantics { } else Modifier)) {
                ZivPageStack(settingsPages, root = {
                    ZivHomeScaffold(style = navigationStyle, bottomBar = {
                        ZivNavigationBar(
                            items = listOf(
                                ZivNavigationItem(stringResource(io.github.joyelliot.zivplayer.feature.library.R.string.library_video), ZivPlaybackIcon.PLAY, "app_video"),
                                ZivNavigationItem(stringResource(io.github.joyelliot.zivplayer.feature.library.R.string.library_audio), ZivPlaybackIcon.AUDIO, "app_audio"),
                                ZivNavigationItem(stringResource(io.github.joyelliot.zivplayer.feature.player.R.string.player_queue_title), ZivPlaybackIcon.LIST, "app_queue"),
                                ZivNavigationItem(stringResource(io.github.joyelliot.zivplayer.feature.settings.R.string.settings_title), ZivPlaybackIcon.SETTINGS, "app_settings"),
                            ),
                            selectedIndex = homePages.selectedPage,
                            onSelect = { index -> selectHome(homeDestinations[index]) },
                            nowPlaying = if (playerState.hasMedia) ({ MiniPlayer(playerState, ::openPlayer, onPlayPause,
                                Modifier.playerExpansionDrag(presentation, travel, ::settlePresentation), videoAnchor = {
                                    MiniVideoAnchor(navigationStyle) { miniFrame = it }
                                }) }) else null,
                            nowPlayingProgress = playerState.durationMs?.takeIf { it > 0 }?.let { playerState.timelineProgress },
                        )
                    }) { bottomPadding ->
                        HomePager(homePages) { page ->
                            val pageDestination = homeDestinations[page]
                            pageState.SaveableStateProvider(pageDestination.name) {
                                when (pageDestination) {
                                    AppDestination.VIDEO, AppDestination.AUDIO -> {
                                        LibraryScreen(
                                            folders = library?.folders?.value.orEmpty(), media = library?.media?.value.orEmpty(),
                                            scanningFolderId = library?.progress?.value?.folderId, scannedCount = library?.progress?.value?.mediaFound ?: 0,
                                            message = library?.message?.value ?: selectionNotice?.toUserMessage(), onAddFolder = { openFolder.launch(Unit) },
                                            onScan = { library?.scan(it) }, onCancelScan = { library?.cancelScan() },
                                            onRemoveFolder = { library?.removeFolder(it) }, onFavorite = { library?.setFavorite(it) }, onHidden = { library?.setHidden(it) },
                                            onOpen = { item -> openPlayerFrom(pageDestination); onDocumentSelected(OpenMediaDocumentResult(Uri.parse(item.sourceUri.value), 0)) },
                                            onOpenQueue = { items, index -> openPlayerFrom(pageDestination); onLibraryPlaylist(items, index) },
                                            onOpenFile = { openMedia.launch(arrayOf(if (pageDestination == AppDestination.AUDIO) "audio/*" else "video/*")) },
                                            onHistory = { selectHome(AppDestination.QUEUE) },
                                            onSettings = { selectHome(AppDestination.SETTINGS) },
                                            onBrowseBackChanged = { libraryBackHandlers[pageDestination] = it },
                                            foldersLoaded = library?.foldersLoaded?.value ?: true,
                                            bottomPadding = bottomPadding,
                                            mediaKind = if (pageDestination == AppDestination.AUDIO) LibraryMediaKind.AUDIO else LibraryMediaKind.VIDEO,
                                            active = !expanded && !settingsPages.canPop && page == homePages.selectedPage,
                                        )
                                    }
                                    AppDestination.QUEUE -> PlaybackQueueScreen(playerState, recentMedia, onSelectQueueItem,
                                        { openPlayerFrom(pageDestination); onOpenRecent(it) }, onForgetRecent,
                                        selectionNotice?.toUserMessage() ?: if (historyUnavailable) stringResource(R.string.media_history_unavailable) else null,
                                        bottomPadding = bottomPadding, active = !expanded && !settingsPages.canPop && page == homePages.selectedPage)
                                    AppDestination.SETTINGS -> SettingsScreen(
                                        message = configurationMessage ?: settings?.message?.value,
                                        onOpenPage = {
                                            homePages.snapToPage(homeDestinations.indexOf(AppDestination.SETTINGS))
                                            settingsPages.push(it.name)
                                        },
                                        onReset = { settings?.update { PlayerPreferences() } },
                                        bottomPadding = bottomPadding,
                                    )
                                    AppDestination.PLAYER -> Unit
                                }
                            }
                        }
                    }
                }, page = { entry ->
                    val page = SettingsPage.valueOf(entry.route)
                    if (page == SettingsPage.DIAGNOSTICS) DiagnosticsScreen(
                        snapshot = diagnostics, device = "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL} · Android ${android.os.Build.VERSION.RELEASE}",
                        message = diagnosticsMessage, onBack = { settingsPages.pop() }, onExport = onExportDiagnostics,
                    ) else SettingsDetailScreen(
                        page = page, value = preferences, resources = settings?.resources?.value.orEmpty(),
                        message = configurationMessage ?: settings?.message?.value, busy = settings?.busy?.value ?: false,
                        onUpdate = { settings?.update(it) },
                        onImport = { kind -> importKind = kind; importResource.launch(arrayOf("*/*")) },
                        onDelete = { settings?.deleteResource(it) }, onBack = { settingsPages.pop() },
                        onOpenPage = { settingsPages.push(it.name) },
                    )
                })
            }
            Box(Modifier.matchParentSize().graphicsLayer { alpha = progress }.background(Color.Black)
                .then(if (expanded) Modifier.pointerInput(Unit) {
                    awaitPointerEventScope { while (true) awaitPointerEvent().changes.forEach { it.consume() } }
                } else Modifier))
            if (playerState.hasMedia) {
                PersistentVideoStage(controller, playerState.canRenderVideo, playerState.videoAspectRatio,
                    window, frame, progress, visible = playerState.hasVideo && (expanded || !settingsPages.canPop),
                    miniCornerRadius = if (navigationStyle == ZivNavigationStyle.LIQUID_GLASS) 12.dp else 8.dp,
                    onBounds = onVideoBounds)
                if (!pictureInPicture && (expanded || !settingsPages.canPop) &&
                    (progress < 0.999f || presentation.dragging) && playerState.hasVideo)
                    VideoExpansionTouchTarget(frame, presentation, travel, ::openPlayer, ::settlePresentation)
            }
            RotatingPlayerViewport(rotation, Modifier
                .then(if (progress < 0.999f || pictureInPicture || presentation.dragging) Modifier.clearAndSetSemantics { } else Modifier)
                .graphicsLayer { alpha = ((progress - 0.65f) / 0.35f).coerceIn(0f, 1f) }
                .layout { measurable, constraints ->
                    val content = measurable.measure(constraints)
                    layout(content.width, content.height) {
                        if (progress > 0.999f && !presentation.dragging && !pictureInPicture && !pictureInPictureTransition) content.place(0, 0)
                    }
                }) {
                FullscreenPlayerScreen(playerState, {}, ::leavePlayer, onPlayPause, onSeekTo,
                    onPlaybackSpeedChange, onVolumeChange, onRepeatModeChange, onSelectTrack, launchSubtitle,
                    onBeginTemporarySpeed, onTemporarySpeedChange, onEndTemporarySpeed, videoOptions, selectVideoFit,
                    interactionEnabled = interactionEnabled && progress > 0.999f && !presentation.dragging,
                    drawBackground = false,
                    contentInsets = playerControlInsets(rotation),
                    onSeekPreviewTo = onSeekPreviewTo, onSeekPreviewFinished = onSeekPreviewFinished,
                    canPrevious = playerState.canPrevious, canNext = playerState.canNext, onPrevious = onPrevious, onNext = onNext,
                    onQueue = { showQueue = true }, queueVisible = showQueue,
                    brightness = brightness, onBrightnessChange = onBrightnessChange,
                    onRotate = { onFullscreenChange(!fullscreen) },
                    onPictureInPicture = onEnterPictureInPicture,
                    onOpenMedia = { openMedia.launch(arrayOf("video/*", "audio/*")) }, onStop = onStop,
                    statusMessage = selectionNotice?.toUserMessage(),
                    exitDescription = stringResource(if (playerReturnDestination in listOf(AppDestination.VIDEO, AppDestination.AUDIO))
                        io.github.joyelliot.zivplayer.feature.library.R.string.library_back_folders
                    else io.github.joyelliot.zivplayer.feature.player.R.string.player_back))
                AnimatedVisibility(showQueue && progress > 0.999f && !presentation.dragging && !pictureInPicture && !pictureInPictureTransition,
                    enter = fadeIn(tween(160)), exit = fadeOut(tween(120))) {
                    PlayerQueuePanel(playerState, { showQueue = false }, onSelectQueueItem, playerControlInsets(rotation))
                }
            }
            if ((progress > 0.999f && !presentation.dragging || presentation.dragging && presentation.dragSource == "handle") &&
                !pictureInPicture && !pictureInPictureTransition) {
                Box(Modifier.align(Alignment.TopCenter).size(72.dp, 22.dp)
                    .playerExpansionDrag(presentation, travel, ::settlePresentation, source = "handle"), contentAlignment = Alignment.Center) {
                    Box(Modifier.size(32.dp, 3.dp).background(Color.White.copy(alpha = 0.4f), androidx.compose.foundation.shape.RoundedCornerShape(2.dp)))
                }
            }
        }
    }
}

private enum class AppDestination { VIDEO, AUDIO, PLAYER, SETTINGS, QUEUE }
private val homeDestinations = listOf(AppDestination.VIDEO, AppDestination.AUDIO, AppDestination.QUEUE, AppDestination.SETTINGS)

@Composable
private fun MediaSelectionNotice.toUserMessage(): String = when (this) {
    MediaSelectionNotice.SESSION_ONLY_PERMISSION ->
        stringResource(R.string.media_session_only_permission)

    MediaSelectionNotice.SESSION_ONLY_HISTORY ->
        stringResource(R.string.media_session_only_history)

    MediaSelectionNotice.OPEN_FAILED -> stringResource(R.string.media_open_failed)
    MediaSelectionNotice.QUEUE_TOO_LARGE -> stringResource(R.string.media_queue_too_large)
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

private class PlaybackControllerDisconnectedException : IllegalStateException(
    "Playback service disconnected while handling a media request.",
)

private const val COMMAND_WAIT_TIMEOUT_MS = 20_000L
