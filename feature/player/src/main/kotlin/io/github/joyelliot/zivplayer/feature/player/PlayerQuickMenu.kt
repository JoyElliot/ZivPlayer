// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*

data class PlayerQuickOption(val id: String, val label: String, val selected: Boolean)
enum class PlayerQuickPage { MAIN, SPEED, AUDIO, SUBTITLE, VIDEO }

@Composable
fun PlayerQuickMenu(
    state: PlayerUiState,
    onDismiss: () -> Unit,
    onPlaybackSpeedChange: (Float) -> Unit,
    onVolumeChange: (Float) -> Unit,
    onRepeatModeChange: (PlayerRepeatMode) -> Unit,
    onSelectTrack: (PlayerTrackKind, String?) -> Unit,
    onOpenSubtitle: () -> Unit,
    videoOptions: List<PlayerQuickOption> = emptyList(),
    onVideoOptionSelected: (String) -> Unit = {},
    initialPage: PlayerQuickPage = PlayerQuickPage.MAIN,
    onQueue: (() -> Unit)? = null,
    onPictureInPicture: (() -> Unit)? = null,
    onOpenMedia: (() -> Unit)? = null,
    onStop: (() -> Unit)? = null,
) {
    var page by rememberSaveable(initialPage) { mutableStateOf(initialPage) }
    val title = stringResource(when (page) {
        PlayerQuickPage.MAIN -> R.string.player_quick_menu
        PlayerQuickPage.SPEED -> R.string.player_speed_title
        PlayerQuickPage.AUDIO -> R.string.player_audio_tracks
        PlayerQuickPage.SUBTITLE -> R.string.player_subtitles
        PlayerQuickPage.VIDEO -> R.string.player_video_fit
    })
    PlayerSidePanel(title, stringResource(R.string.player_close_menu), onDismiss,
        onBack = if (page != PlayerQuickPage.MAIN) ({ page = PlayerQuickPage.MAIN }) else null) {
        key(page) {
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(top = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when (page) {
                    PlayerQuickPage.MAIN -> {
                        Row(Modifier.fillMaxWidth()) {
                            QuickTile(ZivPlaybackIcon.SPEED, stringResource(R.string.player_speed_title), Modifier.weight(1f), state.canSetSpeed) { page = PlayerQuickPage.SPEED }
                            QuickTile(ZivPlaybackIcon.AUDIO, stringResource(R.string.player_audio_tracks), Modifier.weight(1f)) { page = PlayerQuickPage.AUDIO }
                            QuickTile(ZivPlaybackIcon.SUBTITLES, stringResource(R.string.player_subtitles), Modifier.weight(1f)) { page = PlayerQuickPage.SUBTITLE }
                        }
                        Row(Modifier.fillMaxWidth()) {
                            QuickTile(ZivPlaybackIcon.FIT, stringResource(R.string.player_video_fit), Modifier.weight(1f), videoOptions.isNotEmpty()) { page = PlayerQuickPage.VIDEO }
                            QuickTile(ZivPlaybackIcon.REPEAT, stringResource(when (state.repeatMode) {
                                PlayerRepeatMode.OFF -> R.string.player_repeat_off
                                PlayerRepeatMode.ONE -> R.string.player_repeat_one
                                PlayerRepeatMode.ALL -> R.string.player_repeat_all
                            }), Modifier.weight(1f), state.canSetRepeat) {
                                onRepeatModeChange(PlayerRepeatMode.entries[(state.repeatMode.ordinal + 1) % PlayerRepeatMode.entries.size])
                            }
                            QuickTile(ZivPlaybackIcon.LIST, stringResource(R.string.player_queue), Modifier.weight(1f), onQueue != null) { onQueue?.invoke() }
                        }
                        Spacer(Modifier.height(4.dp))
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            ZivPlaybackGlyph(ZivPlaybackIcon.VOLUME, Modifier.size(20.dp))
                            ZivPlaybackText(stringResource(R.string.player_volume), Modifier.padding(start = 10.dp).weight(1f))
                            ZivPlaybackText("${(state.volume * 100).toInt()}%", secondary = true, compact = true)
                        }
                        var volume by remember(state.volume) { mutableFloatStateOf(state.volume.coerceIn(0f, 1f)) }
                        ZivPlaybackSlider(volume, { volume = it }, { onVolumeChange(volume) }, Modifier.fillMaxWidth(),
                            state.canSetVolume, description = stringResource(R.string.player_volume), onCancel = { volume = state.volume })
                        onPictureInPicture?.let { action -> ChoiceRow(stringResource(R.string.player_pip), false, state.canRenderVideo && state.hasVideo) { onDismiss(); action() } }
                        onOpenMedia?.let { action -> ChoiceRow(stringResource(R.string.player_open_media), false) { onDismiss(); action() } }
                        onStop?.let { action -> ChoiceRow(stringResource(R.string.player_stop), false, state.canStop) { onDismiss(); action() } }
                        ZivPlaybackText(stringResource(R.string.player_gesture_help), Modifier.padding(horizontal = 4.dp), secondary = true, compact = true, maxLines = 6)
                    }
                    PlayerQuickPage.SPEED -> {
                        listOf(0.25f, 0.5f, 0.75f, 1f, 1.25f, 1.5f, 2f, 3f, 4f).chunked(3).forEach { row ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                row.forEach { speed ->
                                    Box(Modifier.weight(1f).height(52.dp).semantics { selected = state.playbackSpeed == speed }
                                        .background(if (state.playbackSpeed == speed) Color(0x3352B9FF) else Color.White.copy(alpha = 0.06f), RoundedCornerShape(8.dp))
                                        .clickable(enabled = state.canSetSpeed, role = Role.RadioButton) { onPlaybackSpeedChange(speed) },
                                        contentAlignment = Alignment.Center) { ZivPlaybackText(formatPlaybackSpeed(speed), secondary = !state.canSetSpeed) }
                                }
                            }
                        }
                        ZivPlaybackText(stringResource(R.string.player_speed_temporary_hint), Modifier.padding(top = 12.dp), secondary = true, compact = true, maxLines = 3)
                    }
                    PlayerQuickPage.AUDIO, PlayerQuickPage.SUBTITLE -> {
                        val kind = if (page == PlayerQuickPage.AUDIO) PlayerTrackKind.AUDIO else PlayerTrackKind.SUBTITLE
                        val tracks = state.tracks.filter { it.kind == kind }
                        if (tracks.isEmpty()) ZivPlaybackText(stringResource(R.string.player_no_tracks), Modifier.padding(12.dp), secondary = true)
                        if (kind == PlayerTrackKind.SUBTITLE) ChoiceRow(stringResource(R.string.player_subtitles_off), tracks.none { it.selected }, state.canSelectTracks) { onSelectTrack(kind, null) }
                        tracks.forEach { track -> ChoiceRow(track.label, track.selected, state.canSelectTracks) { onSelectTrack(kind, track.id) } }
                        if (kind == PlayerTrackKind.SUBTITLE) {
                            Spacer(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(alpha = 0.12f)))
                            ChoiceRow(stringResource(R.string.player_open_subtitle), false, state.canAddSubtitle) { onDismiss(); onOpenSubtitle() }
                            state.subtitleMessage?.let { ZivPlaybackText(it, Modifier.padding(12.dp), secondary = true) }
                        }
                    }
                    PlayerQuickPage.VIDEO -> videoOptions.forEach { option -> ChoiceRow(option.label, option.selected) { onVideoOptionSelected(option.id) } }
                }
            }
        }
    }
}

@Composable
private fun QuickTile(icon: ZivPlaybackIcon, label: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Column(modifier.clickable(enabled = enabled, role = Role.Button, onClick = onClick).padding(vertical = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.size(44.dp).background(Color.White.copy(alpha = 0.06f), RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
            ZivPlaybackGlyph(icon, Modifier.size(23.dp), enabled)
        }
        ZivPlaybackText(label, Modifier.padding(horizontal = 2.dp), secondary = !enabled, compact = true, maxLines = 2, textAlign = TextAlign.Center)
    }
}

@Composable
private fun ChoiceRow(label: String, checked: Boolean, enabled: Boolean = true, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).semantics { selected = checked }
        .background(if (checked) Color(0x2252B9FF) else Color.Transparent, RoundedCornerShape(6.dp))
        .clickable(enabled = enabled, role = Role.RadioButton, onClick = onClick).padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        ZivPlaybackText(label, Modifier.weight(1f), secondary = !enabled, maxLines = 3)
        if (checked) ZivPlaybackGlyph(ZivPlaybackIcon.CHECK, Modifier.padding(start = 12.dp).size(20.dp))
    }
}
