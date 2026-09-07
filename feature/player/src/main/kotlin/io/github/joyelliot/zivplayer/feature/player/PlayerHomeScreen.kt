// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.ZivCard
import io.github.joyelliot.zivplayer.designsystem.ZivLinearProgress
import io.github.joyelliot.zivplayer.designsystem.ZivLoadingIndicator
import io.github.joyelliot.zivplayer.designsystem.ZivPreviewTheme
import io.github.joyelliot.zivplayer.designsystem.ZivPrimaryButton
import io.github.joyelliot.zivplayer.designsystem.ZivScreen
import io.github.joyelliot.zivplayer.designsystem.ZivSlider
import io.github.joyelliot.zivplayer.designsystem.ZivStatusText
import io.github.joyelliot.zivplayer.designsystem.ZivText

@Composable
fun PlayerHomeScreen(
    state: PlayerUiState = PlayerUiState(),
    recentMedia: List<RecentMediaUiItem> = emptyList(),
    statusMessage: String? = null,
    onOpenMedia: () -> Unit = {},
    onPlayPause: () -> Unit = {},
    onStop: () -> Unit = {},
    onSeekTo: (Long) -> Unit = {},
    onPlaybackSpeedChange: (Float) -> Unit = {},
    onVolumeChange: (Float) -> Unit = {},
    onRepeatModeChange: (PlayerRepeatMode) -> Unit = {},
    onSelectTrack: (PlayerTrackKind, String?) -> Unit = { _, _ -> },
    onOpenSubtitle: () -> Unit = {},
    onOpenRecent: (String) -> Unit = {},
    onForgetRecent: (String) -> Unit = {},
    videoContent: @Composable () -> Unit = {},
    extraControls: @Composable () -> Unit = {},
    showInlineOptions: Boolean = true,
) {
    ZivScreen(title = stringResource(R.string.player_title)) {
        ZivCard {
            Box(modifier = Modifier.fillMaxWidth()) {
                videoContent()
            }
        }

        PlayerIdentity(state)
        PlayerStatus(state)
        statusMessage?.let { ZivStatusText(text = it) }
        PlayerTimeline(state = state, onSeekTo = onSeekTo)
        PlayerTransportControls(
            state = state,
            onOpenMedia = onOpenMedia,
            onPlayPause = onPlayPause,
            onStop = onStop,
        )
        extraControls()
        if (showInlineOptions) {
        PlayerSecondaryControls(
            state = state,
            onPlaybackSpeedChange = onPlaybackSpeedChange,
            onVolumeChange = onVolumeChange,
            onRepeatModeChange = onRepeatModeChange,
        )
        PlayerTrackControls(state, onSelectTrack, onOpenSubtitle)
        }
        RecentMediaSection(
            recentMedia = recentMedia,
            currentMediaId = state.mediaId,
            onOpenRecent = onOpenRecent,
            onForgetRecent = onForgetRecent,
        )
    }
}

@Composable
private fun PlayerTrackControls(
    state: PlayerUiState,
    onSelectTrack: (PlayerTrackKind, String?) -> Unit,
    onOpenSubtitle: () -> Unit,
) {
    if (!state.hasMedia) return
    var expanded by remember(state.mediaId) { mutableStateOf<PlayerTrackKind?>(null) }
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        PlayerTrackKind.entries.forEach { kind ->
            ZivPrimaryButton(
                text = stringResource(if (kind == PlayerTrackKind.AUDIO) R.string.player_audio_tracks else R.string.player_subtitles),
                onClick = { expanded = if (expanded == kind) null else kind },
                enabled = state.connectionStatus == PlayerConnectionStatus.CONNECTED,
                modifier = Modifier.weight(1f),
            )
        }
    }
    val kind = expanded ?: return
    val tracks = state.tracks.filter { it.kind == kind }
    ZivCard {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (tracks.isEmpty()) ZivText(stringResource(R.string.player_no_tracks))
            tracks.forEach { track ->
                ZivPrimaryButton(
                    text = if (track.selected) stringResource(R.string.player_selected_track, track.label) else track.label,
                    onClick = { onSelectTrack(kind, track.id) },
                    enabled = state.canSelectTracks && !track.selected,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            if (kind == PlayerTrackKind.SUBTITLE) {
                ZivPrimaryButton(
                    text = stringResource(R.string.player_subtitles_off),
                    onClick = { onSelectTrack(PlayerTrackKind.SUBTITLE, null) },
                    enabled = state.canSelectTracks && tracks.any { it.selected },
                    modifier = Modifier.fillMaxWidth(),
                )
                ZivPrimaryButton(
                    text = stringResource(R.string.player_open_subtitle), onClick = onOpenSubtitle,
                    enabled = state.canAddSubtitle, modifier = Modifier.fillMaxWidth(),
                )
                state.subtitleMessage?.let { ZivStatusText(it) }
            }
        }
    }
}

@Composable
private fun PlayerIdentity(state: PlayerUiState) {
    ZivText(text = state.title ?: stringResource(R.string.player_no_media_title))
    state.supportingText?.let { ZivText(text = it) }
}

@Composable
private fun PlayerStatus(state: PlayerUiState) {
    val status = when {
        state.connectionStatus == PlayerConnectionStatus.CONNECTING ->
            stringResource(R.string.player_status_connecting)

        state.connectionStatus == PlayerConnectionStatus.RECONNECTING ->
            stringResource(R.string.player_status_reconnecting)

        state.playbackStatus == PlayerPlaybackStatus.EMPTY ->
            stringResource(R.string.player_status_empty)

        state.playbackStatus == PlayerPlaybackStatus.LOADING ->
            stringResource(R.string.player_status_loading)

        state.playbackStatus == PlayerPlaybackStatus.PLAYING ->
            stringResource(R.string.player_status_playing)

        state.playbackStatus == PlayerPlaybackStatus.PAUSED ->
            stringResource(R.string.player_status_paused)

        state.playbackStatus == PlayerPlaybackStatus.STOPPED ->
            stringResource(R.string.player_status_stopped)

        state.playbackStatus == PlayerPlaybackStatus.BUFFERING ->
            stringResource(R.string.player_status_buffering)

        state.playbackStatus == PlayerPlaybackStatus.ENDED ->
            stringResource(R.string.player_status_ended)

        else -> state.errorMessage ?: stringResource(R.string.player_status_error)
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (
            state.connectionStatus != PlayerConnectionStatus.CONNECTED ||
            state.playbackStatus == PlayerPlaybackStatus.LOADING ||
            state.playbackStatus == PlayerPlaybackStatus.BUFFERING
        ) {
            ZivLoadingIndicator()
        }
        ZivStatusText(text = status)
    }
}

@Composable
private fun PlayerTimeline(
    state: PlayerUiState,
    onSeekTo: (Long) -> Unit,
) {
    val duration = state.durationMs?.takeIf { it > 0L }
    var pendingProgress by remember(state.mediaId) { mutableStateOf<Float?>(null) }
    val progress = pendingProgress ?: state.timelineProgress
    val shownPosition = duration?.let { (progress * it).toLong().coerceIn(0L, it) }
        ?: state.positionMs
    val elapsed = formatPlaybackTime(shownPosition)
    val total = duration?.let(::formatPlaybackTime) ?: stringResource(R.string.player_time_unknown)

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        duration?.let { timelineDuration ->
            state.bufferedPositionMs?.let { buffered ->
                ZivLinearProgress(
                    progress = (buffered.toDouble() / timelineDuration.toDouble()).toFloat(),
                )
            }
        }
        ZivSlider(
            value = progress,
            onValueChange = { pendingProgress = it },
            enabled = state.canSeek && duration != null,
            onValueChangeFinished = {
                val target = duration?.let { (progress * it).toLong().coerceIn(0L, it) }
                pendingProgress = null
                target?.let(onSeekTo)
            },
            stateDescription = stringResource(
                R.string.player_timeline_description,
                elapsed,
                total,
            ),
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            ZivText(text = elapsed)
            ZivText(text = total)
        }
    }
}

@Composable
private fun PlayerTransportControls(
    state: PlayerUiState,
    onOpenMedia: () -> Unit,
    onPlayPause: () -> Unit,
    onStop: () -> Unit,
) {
    val playLabel = when {
        state.playbackStatus == PlayerPlaybackStatus.ENDED ->
            stringResource(R.string.player_replay)

        state.playbackStatus == PlayerPlaybackStatus.PLAYING ||
            state.playbackStatus == PlayerPlaybackStatus.BUFFERING && state.playWhenReady ->
            stringResource(R.string.player_pause)

        else -> stringResource(R.string.player_play)
    }
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ZivPrimaryButton(
            text = playLabel,
            onClick = onPlayPause,
            modifier = Modifier.weight(1f),
            enabled = state.canPlayPause,
        )
        ZivPrimaryButton(
            text = stringResource(R.string.player_stop),
            onClick = onStop,
            modifier = Modifier.weight(1f),
            enabled = state.canStop,
        )
        ZivPrimaryButton(
            text = stringResource(R.string.player_open_media),
            onClick = onOpenMedia,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun PlayerSecondaryControls(
    state: PlayerUiState,
    onPlaybackSpeedChange: (Float) -> Unit,
    onVolumeChange: (Float) -> Unit,
    onRepeatModeChange: (PlayerRepeatMode) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ZivPrimaryButton(
            text = stringResource(
                R.string.player_speed,
                formatPlaybackSpeed(state.playbackSpeed),
            ),
            onClick = { onPlaybackSpeedChange(nextPlaybackSpeed(state.playbackSpeed)) },
            modifier = Modifier.weight(1f),
            enabled = state.canSetSpeed,
        )
        ZivPrimaryButton(
            text = stringResource(when (state.repeatMode) {
                PlayerRepeatMode.OFF -> R.string.player_repeat_off
                PlayerRepeatMode.ONE -> R.string.player_repeat_one
                PlayerRepeatMode.ALL -> R.string.player_repeat_all
            }),
            onClick = { onRepeatModeChange(PlayerRepeatMode.entries[(state.repeatMode.ordinal + 1) % PlayerRepeatMode.entries.size]) },
            modifier = Modifier.weight(1f),
            enabled = state.canSetRepeat,
        )
    }

    var pendingVolume by remember(state.mediaId) { mutableStateOf<Float?>(null) }
    val volume = pendingVolume ?: state.volume.coerceIn(0f, 1f)
    ZivText(text = stringResource(R.string.player_volume))
    ZivSlider(
        value = volume,
        onValueChange = { pendingVolume = it },
        enabled = state.canSetVolume,
        onValueChangeFinished = {
            pendingVolume?.let(onVolumeChange)
            pendingVolume = null
        },
        stateDescription = stringResource(
            R.string.player_volume_description,
            (volume * 100f).toInt(),
        ),
    )
}

@Composable
private fun RecentMediaSection(
    recentMedia: List<RecentMediaUiItem>,
    currentMediaId: String?,
    onOpenRecent: (String) -> Unit,
    onForgetRecent: (String) -> Unit,
) {
    ZivText(text = stringResource(R.string.player_recent_title))
    if (recentMedia.isEmpty()) {
        ZivText(text = stringResource(R.string.player_recent_empty))
        return
    }

    recentMedia.forEach { item ->
        ZivCard {
            Column(
                modifier = Modifier.padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                ZivText(text = item.title)
                item.supportingText?.let { ZivText(text = it) }
                item.progress?.let { progress -> ZivLinearProgress(progress = progress) }
                when {
                    item.completed -> ZivText(
                        text = stringResource(R.string.player_recent_completed),
                    )

                    item.positionMs != null -> ZivText(
                        text = stringResource(
                            R.string.player_recent_resume,
                            formatPlaybackTime(item.positionMs),
                        ),
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    ZivPrimaryButton(
                        text = stringResource(R.string.player_recent_open),
                        onClick = { onOpenRecent(item.mediaId) },
                        modifier = Modifier.weight(1f),
                        enabled = item.mediaId != currentMediaId,
                    )
                    ZivPrimaryButton(
                        text = stringResource(R.string.player_recent_forget),
                        onClick = { onForgetRecent(item.mediaId) },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun PlayerHomeScreenPreview() {
    ZivPreviewTheme {
        PlayerHomeScreen(
            state = PlayerUiState(
                connectionStatus = PlayerConnectionStatus.CONNECTED,
                playbackStatus = PlayerPlaybackStatus.PAUSED,
                mediaId = "preview",
                title = "Example video.mp4",
                positionMs = 42_000L,
                durationMs = 180_000L,
                bufferedPositionMs = 75_000L,
                canPlayPause = true,
                canStop = true,
                canSeek = true,
                canSetSpeed = true,
                canSetVolume = true,
                canSetRepeat = true,
            ),
        )
    }
}
