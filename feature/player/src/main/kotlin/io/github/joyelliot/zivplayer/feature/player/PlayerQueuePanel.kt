// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*

@Composable
fun PlayerQueuePanel(state: PlayerUiState, onDismiss: () -> Unit, onSelect: (Int) -> Unit) {
    PlayerSidePanel(stringResource(R.string.player_queue_count, state.queue.size),
        stringResource(R.string.player_close_queue), onDismiss) {
        if (state.queue.isEmpty()) ZivPlaybackText(stringResource(R.string.player_queue_empty), Modifier.padding(12.dp), secondary = true)
        val selected = state.queue.indexOfFirst { it.selected }.coerceAtLeast(0)
        val scroll = rememberLazyListState(selected)
        LaunchedEffect(selected) { if (state.queue.isNotEmpty()) scroll.animateScrollToItem(selected) }
        LazyColumn(Modifier.weight(1f).padding(top = 8.dp), state = scroll, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            items(state.queue, key = { it.index }) { item ->
                Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).semantics { this.selected = item.selected }
                    .background(if (item.selected) Color(0x2252B9FF) else Color.Transparent, RoundedCornerShape(6.dp))
                    .clickable(enabled = state.canSelectQueueItem, role = Role.RadioButton) { onSelect(item.index); onDismiss() }
                    .padding(horizontal = 12.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    ZivPlaybackText("${item.index + 1}", Modifier.width(28.dp), secondary = true, compact = true)
                    ZivPlaybackText(item.title, Modifier.weight(1f), secondary = !item.selected, maxLines = 3)
                    if (item.selected) ZivPlaybackGlyph(ZivPlaybackIcon.PLAY, Modifier.padding(start = 8.dp).size(18.dp))
                }
            }
        }
    }
}
