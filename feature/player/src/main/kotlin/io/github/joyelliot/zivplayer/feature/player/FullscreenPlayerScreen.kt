// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*
import kotlinx.coroutines.delay

@Composable
fun FullscreenPlayerScreen(
    state: PlayerUiState,
    videoContent: @Composable () -> Unit,
    onExit: () -> Unit,
    onPlayPause: () -> Unit,
    onSeekTo: (Long) -> Unit,
    onPlaybackSpeedChange: (Float) -> Unit,
    onVolumeChange: (Float) -> Unit,
    onRepeatModeChange: (PlayerRepeatMode) -> Unit,
    onSelectTrack: (PlayerTrackKind, String?) -> Unit,
    onOpenSubtitle: () -> Unit,
    onBeginTemporarySpeed: () -> Long?,
    onTemporarySpeedChange: (Long, Float) -> Unit,
    onEndTemporarySpeed: (Long) -> Unit,
    videoOptions: List<PlayerQuickOption> = emptyList(),
    onVideoOptionSelected: (String) -> Unit = {},
    interactionEnabled: Boolean = true,
    canPrevious: Boolean = false,
    canNext: Boolean = false,
    onPrevious: () -> Unit = {},
    onNext: () -> Unit = {},
    onQueue: () -> Unit = {},
    queueVisible: Boolean = false,
    onRotate: (() -> Unit)? = null,
    onPictureInPicture: (() -> Unit)? = null,
    onOpenMedia: (() -> Unit)? = null,
    onStop: (() -> Unit)? = null,
    statusMessage: String? = null,
    exitDescription: String? = null,
    modifier: Modifier = Modifier,
) {
    var controlsVisible by remember(state.playbackIdentity) { mutableStateOf(true) }
    var menuPage by remember(state.playbackIdentity) { mutableStateOf<PlayerQuickPage?>(null) }
    var locked by remember(state.playbackIdentity) { mutableStateOf(false) }
    var unlockVisible by remember { mutableStateOf(true) }
    var gestureActive by remember { mutableStateOf(false) }
    var seekPreview by remember(state.playbackIdentity) { mutableStateOf<Long?>(null) }
    var speedPreview by remember(state.playbackIdentity) { mutableStateOf<Float?>(null) }
    var sliderPosition by remember(state.playbackIdentity) { mutableStateOf<Float?>(null) }
    var interactionRevision by remember { mutableIntStateOf(0) }
    fun reveal() { controlsVisible = true; interactionRevision++ }
    fun openMenu(page: PlayerQuickPage) { reveal(); menuPage = page }

    LaunchedEffect(state.playWhenReady, state.playbackStatus, state.errorMessage) {
        if (!state.playWhenReady || state.errorMessage != null || state.playbackStatus == PlayerPlaybackStatus.ENDED) controlsVisible = true
        if (state.errorMessage != null) locked = false
    }
    LaunchedEffect(controlsVisible, state.playbackIdentity, state.playWhenReady, state.playbackStatus,
        state.errorMessage, gestureActive, sliderPosition, menuPage, queueVisible, interactionRevision, interactionEnabled) {
        if (controlsVisible && state.playWhenReady && !gestureActive && sliderPosition == null && menuPage == null &&
            !queueVisible && interactionEnabled && state.errorMessage == null) {
            delay(3_500)
            controlsVisible = false
        }
    }
    LaunchedEffect(locked, unlockVisible, interactionRevision) {
        if (locked && unlockVisible) { delay(3_000); unlockVisible = false }
    }
    ZivTheme(appearance = ZivAppearance.DARK) {
        BoxWithConstraints(modifier.fillMaxSize().background(Color.Black)) {
            val safeInsets = WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal).asPaddingValues()
            val direction = LocalLayoutDirection.current
            val controlsWidth = maxWidth - safeInsets.calculateLeftPadding(direction) - safeInsets.calculateRightPadding(direction) - 40.dp
            val compact = controlsWidth < 480.dp
            videoContent()
            val audioReady = state.hasMedia && !state.hasVideo && state.playbackStatus in
                listOf(PlayerPlaybackStatus.PLAYING, PlayerPlaybackStatus.PAUSED, PlayerPlaybackStatus.ENDED)
            if (audioReady) Column(Modifier.align(Alignment.TopCenter).statusBarsPadding()
                .padding(top = if (compact) 120.dp else 72.dp, start = 32.dp, end = 32.dp),
                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(20.dp)) {
                ZivPlaybackGlyph(ZivPlaybackIcon.AUDIO, Modifier.size(72.dp))
                ZivPlaybackText(state.title.orEmpty(), title = true, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
            PlayerVideoGestures(state, interactionEnabled && !locked && menuPage == null && !queueVisible,
                onToggleControls = { controlsVisible = !controlsVisible; interactionRevision++ }, onPlayPause = onPlayPause,
                onSeekTo = onSeekTo, onBeginTemporarySpeed = onBeginTemporarySpeed,
                onTemporarySpeedChange = onTemporarySpeedChange, onEndTemporarySpeed = onEndTemporarySpeed,
                onSeekPreview = { seekPreview = it }, onSpeedPreview = { speedPreview = it },
                onGestureActive = { gestureActive = it; if (!it) interactionRevision++ },
                modifier = Modifier.matchParentSize())
            if (controlsVisible && !locked && !gestureActive && menuPage == null && !queueVisible) {
                Row(Modifier.align(Alignment.TopCenter).fillMaxWidth()
                    .background(Brush.verticalGradient(listOf(Color.Black.copy(alpha = 0.6f), Color.Transparent)))
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
                    .padding(start = 6.dp, end = 10.dp, top = 4.dp, bottom = 14.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    ZivPlaybackIconButton(ZivPlaybackIcon.BACK, exitDescription ?: stringResource(R.string.player_exit_fullscreen), onExit)
                    ZivPlaybackText(state.title ?: stringResource(R.string.player_title),
                        Modifier.weight(1f).padding(start = 8.dp, end = 12.dp), title = true, compact = true, maxLines = 1)
                    if (!compact) {
                        ZivPlaybackIconButton(ZivPlaybackIcon.AUDIO, stringResource(R.string.player_audio_tracks), { openMenu(PlayerQuickPage.AUDIO) })
                        ZivPlaybackIconButton(ZivPlaybackIcon.SUBTITLES, stringResource(R.string.player_subtitles), { openMenu(PlayerQuickPage.SUBTITLE) })
                    }
                    ZivPlaybackIconButton(ZivPlaybackIcon.MORE, stringResource(R.string.player_quick_menu), { openMenu(PlayerQuickPage.MAIN) })
                }
                if (compact) Row(Modifier.align(Alignment.Center), horizontalArrangement = Arrangement.spacedBy(36.dp), verticalAlignment = Alignment.CenterVertically) {
                    ZivPlaybackIconButton(ZivPlaybackIcon.PREVIOUS, stringResource(R.string.player_previous), { reveal(); onPrevious() }, enabled = canPrevious)
                    ZivPlaybackIconButton(if (state.playWhenReady) ZivPlaybackIcon.PAUSE else ZivPlaybackIcon.PLAY,
                        stringResource(if (state.playWhenReady) R.string.player_pause else R.string.player_play),
                        { reveal(); onPlayPause() }, enabled = state.canPlayPause, prominent = true)
                    ZivPlaybackIconButton(ZivPlaybackIcon.NEXT, stringResource(R.string.player_next), { reveal(); onNext() }, enabled = canNext)
                }
                Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                    .background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.7f))))
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom + WindowInsetsSides.Horizontal))
                    .padding(start = 20.dp, end = 20.dp, top = 16.dp, bottom = 4.dp)) {
                    val duration = state.durationMs?.takeIf { it > 0L }
                    val fraction = sliderPosition ?: state.timelineProgress
                    val position = if (sliderPosition != null && duration != null) (fraction * duration).toLong() else state.positionMs
                    val elapsed = formatPlaybackTime(position)
                    val total = duration?.let(::formatPlaybackTime) ?: stringResource(R.string.player_time_unknown)
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        ZivPlaybackText(elapsed, Modifier.widthIn(min = 42.dp), compact = true, maxLines = 1)
                        key(state.playbackIdentity) {
                            ZivPlaybackSlider(fraction, { sliderPosition = it; reveal() }, {
                                val progress = sliderPosition
                                sliderPosition = null
                                if (progress != null && duration != null) onSeekTo((progress * duration).toLong().coerceIn(0L, duration))
                                reveal()
                            }, Modifier.weight(1f).padding(horizontal = 8.dp), state.canSeek && duration != null,
                                bufferedValue = if (duration != null) ((state.bufferedPositionMs ?: state.positionMs).toFloat() / duration).coerceIn(0f, 1f) else 0f,
                                description = stringResource(R.string.player_timeline_description, elapsed, total),
                                onCancel = { sliderPosition = null })
                        }
                        ZivPlaybackText(total, Modifier.widthIn(min = 42.dp), compact = true, maxLines = 1,
                            textAlign = androidx.compose.ui.text.style.TextAlign.End)
                    }
                    Box(Modifier.fillMaxWidth()) {
                        Box(Modifier.fillMaxWidth().height(56.dp)) {
                            Row(Modifier.align(Alignment.CenterStart), verticalAlignment = Alignment.CenterVertically) {
                                ZivPlaybackIconButton(ZivPlaybackIcon.UNLOCK, stringResource(R.string.player_lock_controls), {
                                    locked = true; unlockVisible = true; interactionRevision++
                                })
                                ZivPlaybackTextButton(formatPlaybackSpeed(state.playbackSpeed), { openMenu(PlayerQuickPage.SPEED) }, enabled = state.canSetSpeed)
                            }
                            if (!compact) Row(Modifier.align(Alignment.Center), horizontalArrangement = Arrangement.spacedBy(24.dp), verticalAlignment = Alignment.CenterVertically) {
                                ZivPlaybackIconButton(ZivPlaybackIcon.PREVIOUS, stringResource(R.string.player_previous), { reveal(); onPrevious() }, enabled = canPrevious)
                                ZivPlaybackIconButton(if (state.playWhenReady) ZivPlaybackIcon.PAUSE else ZivPlaybackIcon.PLAY,
                                    stringResource(if (state.playWhenReady) R.string.player_pause else R.string.player_play),
                                    { reveal(); onPlayPause() }, enabled = state.canPlayPause, prominent = true)
                                ZivPlaybackIconButton(ZivPlaybackIcon.NEXT, stringResource(R.string.player_next), { reveal(); onNext() }, enabled = canNext)
                            }
                            Row(Modifier.align(Alignment.CenterEnd)) {
                                if (videoOptions.isNotEmpty()) ZivPlaybackIconButton(ZivPlaybackIcon.FIT, stringResource(R.string.player_video_fit), { openMenu(PlayerQuickPage.VIDEO) })
                                ZivPlaybackIconButton(ZivPlaybackIcon.LIST, stringResource(R.string.player_queue), { reveal(); onQueue() })
                                onRotate?.let { ZivPlaybackIconButton(ZivPlaybackIcon.FULLSCREEN, stringResource(R.string.player_rotate), it) }
                            }
                        }
                    }
                }
            }
            if (locked) {
                Box(Modifier.matchParentSize().pointerInput(Unit) { detectTapGestures { unlockVisible = true; interactionRevision++ } })
                if (unlockVisible) Row(Modifier.align(Alignment.BottomStart)
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom + WindowInsetsSides.Start))
                    .padding(start = 20.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    ZivPlaybackIconButton(ZivPlaybackIcon.LOCK, stringResource(R.string.player_unlock_controls), { locked = false; reveal() })
                }
            }
            val hint = when {
                speedPreview != null -> stringResource(R.string.player_hold_speed, formatPlaybackSpeed(checkNotNull(speedPreview)))
                seekPreview != null -> stringResource(R.string.player_seek_preview, formatPlaybackTime(checkNotNull(seekPreview)), state.durationMs?.let(::formatPlaybackTime).orEmpty())
                else -> null
            }
            if (hint != null) Box(Modifier.align(if (speedPreview != null) Alignment.TopCenter else Alignment.Center)
                .padding(top = 28.dp).background(Color.Black.copy(alpha = 0.65f), RoundedCornerShape(8.dp))
                .padding(horizontal = 18.dp, vertical = 10.dp)) { ZivPlaybackText(hint) }
            if (state.playbackStatus == PlayerPlaybackStatus.LOADING || state.playbackStatus == PlayerPlaybackStatus.BUFFERING) {
                Box(Modifier.align(Alignment.Center)) { ZivLoadingIndicator() }
            }
            state.errorMessage?.let { ZivPlaybackText(it, Modifier.align(Alignment.Center).padding(32.dp)) }
            if (controlsVisible && !locked) statusMessage?.let {
                ZivPlaybackText(it, Modifier.align(Alignment.TopCenter).statusBarsPadding().padding(top = 68.dp, start = 24.dp, end = 24.dp))
            }
            menuPage?.let { page ->
                PlayerQuickMenu(state, { menuPage = null; reveal() }, onPlaybackSpeedChange, onVolumeChange,
                    onRepeatModeChange, onSelectTrack, onOpenSubtitle, videoOptions, onVideoOptionSelected,
                    initialPage = page, onQueue = { menuPage = null; onQueue() },
                    onPictureInPicture = onPictureInPicture, onOpenMedia = onOpenMedia, onStop = onStop)
            }
        }
    }
}
