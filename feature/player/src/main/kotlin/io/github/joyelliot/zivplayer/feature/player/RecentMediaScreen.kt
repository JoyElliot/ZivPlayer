// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import io.github.joyelliot.zivplayer.designsystem.*

@Composable
fun RecentMediaScreen(items: List<RecentMediaUiItem>, onBack: () -> Unit,
    onOpen: (String) -> Unit, onForget: (String) -> Unit, message: String? = null, showBack: Boolean = true,
    bottomPadding: Dp = 0.dp) {
    var actionItem by remember { mutableStateOf<RecentMediaUiItem?>(null) }
    ZivLibrarySurface(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
        ZivPageHeader(stringResource(R.string.player_recent_title), leading = if (showBack) ({
            ZivLibraryIconButton(ZivPlaybackIcon.BACK, stringResource(R.string.player_back), onBack)
        }) else null)
        message?.let { ZivStatusText(it, Modifier.padding(20.dp)) }
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = bottomPadding + 16.dp)) {
            recentMediaItems(items, onOpen) { actionItem = it }
        }
    }
    RecentMediaActions(actionItem, { actionItem = null }, onForget)
}

internal fun LazyListScope.recentMediaItems(items: List<RecentMediaUiItem>, onOpen: (String) -> Unit,
    onActions: (RecentMediaUiItem) -> Unit) {
    if (items.isEmpty()) item("empty_history") {
        ZivLibraryText(stringResource(R.string.player_recent_empty), Modifier.padding(24.dp), secondary = true)
    }
    items(items, key = { "history_${it.mediaId}" }) { item ->
        val detail = if (item.completed) stringResource(R.string.player_recent_completed)
            else item.positionMs?.let { stringResource(R.string.player_recent_resume, formatPlaybackTime(it)) }
        ZivLibraryRow(item.title, detail, ZivPlaybackIcon.HISTORY, { onOpen(item.mediaId) },
            Modifier.testTag("history_${item.mediaId}")) {
            ZivLibraryIconButton(ZivPlaybackIcon.MORE, stringResource(R.string.player_quick_menu), { onActions(item) })
        }
    }
}

@Composable
internal fun RecentMediaActions(item: RecentMediaUiItem?, onDismiss: () -> Unit, onForget: (String) -> Unit) {
    item?.let { ZivLibraryDialog(it.title, onDismiss) {
        ZivLibraryMenuItem(stringResource(R.string.player_recent_forget), { onDismiss(); onForget(it.mediaId) })
    } }
}

@Composable
fun MiniPlayer(state: PlayerUiState, onOpen: () -> Unit, onPlayPause: () -> Unit) {
    if (!state.hasMedia) return
    ZivMiniPlayerSurface(onClick = onOpen,
        progress = state.durationMs?.takeIf { it > 0 }?.let { state.timelineProgress }) {
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("mini_player")
            .padding(start = 8.dp, end = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            ZivLibraryTile(if (state.hasVideo) ZivPlaybackIcon.PLAY else ZivPlaybackIcon.AUDIO, Modifier.size(36.dp))
            Column(Modifier.weight(1f).padding(start = 8.dp, end = 2.dp, top = 4.dp, bottom = 4.dp)) {
                ZivLibraryText(state.title.orEmpty(), maxLines = 1)
                ZivLibraryText("${formatPlaybackTime(state.positionMs)} / ${state.durationMs?.let(::formatPlaybackTime) ?: "--:--"}", secondary = true, maxLines = 1)
            }
            ZivLibraryIconButton(if (state.playWhenReady) ZivPlaybackIcon.PAUSE else ZivPlaybackIcon.PLAY,
                stringResource(if (state.playWhenReady) R.string.player_pause else R.string.player_play), onPlayPause, enabled = state.canPlayPause)
        }
    }
}
