// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*

@Composable
fun RecentMediaScreen(items: List<RecentMediaUiItem>, onBack: () -> Unit,
    onOpen: (String) -> Unit, onForget: (String) -> Unit, message: String? = null) {
    var actionItem by remember { mutableStateOf<RecentMediaUiItem?>(null) }
    ZivLibrarySurface(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            ZivLibraryIconButton(ZivPlaybackIcon.BACK, stringResource(R.string.player_back), onBack)
            ZivLibraryText(stringResource(R.string.player_recent_title), title = true)
        }
        message?.let { ZivStatusText(it, Modifier.padding(20.dp)) }
        LazyColumn(Modifier.fillMaxSize()) {
            if (items.isEmpty()) item { ZivLibraryText(stringResource(R.string.player_recent_empty), Modifier.padding(24.dp), secondary = true) }
            items(items, key = { it.mediaId }) { item ->
                val detail = if (item.completed) stringResource(R.string.player_recent_completed)
                    else item.positionMs?.let { stringResource(R.string.player_recent_resume, formatPlaybackTime(it)) }
                ZivLibraryRow(item.title, detail, ZivPlaybackIcon.HISTORY, { onOpen(item.mediaId) }) {
                    ZivLibraryIconButton(ZivPlaybackIcon.MORE, stringResource(R.string.player_quick_menu), { actionItem = item })
                }
            }
        }
    }
    actionItem?.let { item -> ZivLibraryDialog(item.title, { actionItem = null }) {
        ZivLibraryMenuItem(stringResource(R.string.player_recent_forget), { actionItem = null; onForget(item.mediaId) })
    } }
}

@Composable
fun MiniPlayer(state: PlayerUiState, onOpen: () -> Unit, onPlayPause: () -> Unit, onQueue: () -> Unit) {
    if (!state.hasMedia) return
    ZivCard(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), onClick = onOpen) {
        Row(Modifier.fillMaxWidth().heightIn(min = 64.dp).padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).padding(end = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                ZivLibraryText(state.title.orEmpty(), maxLines = 1)
                ZivLibraryText("${formatPlaybackTime(state.positionMs)} / ${state.durationMs?.let(::formatPlaybackTime) ?: "--:--"}", secondary = true, maxLines = 1)
            }
            ZivLibraryIconButton(if (state.playWhenReady) ZivPlaybackIcon.PAUSE else ZivPlaybackIcon.PLAY,
                stringResource(if (state.playWhenReady) R.string.player_pause else R.string.player_play), onPlayPause, enabled = state.canPlayPause)
            ZivLibraryIconButton(ZivPlaybackIcon.LIST, stringResource(R.string.player_queue), onQueue)
        }
    }
}
