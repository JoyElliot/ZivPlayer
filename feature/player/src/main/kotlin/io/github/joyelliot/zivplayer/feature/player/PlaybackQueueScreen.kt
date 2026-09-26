// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*

/** One scroll surface: the active playlist first, then durable playback history. */
@Composable
fun PlaybackQueueScreen(
    state: PlayerUiState,
    history: List<RecentMediaUiItem>,
    onSelect: (Int) -> Unit,
    onOpenHistory: (String) -> Unit,
    onForgetHistory: (String) -> Unit,
    message: String? = null,
    bottomPadding: Dp = 0.dp,
) {
    var actionItem by remember { mutableStateOf<RecentMediaUiItem?>(null) }
    ZivLibrarySurface(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
        ZivPageHeader(stringResource(R.string.player_queue_title))
        LazyColumn(Modifier.fillMaxSize().testTag("playback_queue_list"),
            contentPadding = PaddingValues(bottom = bottomPadding + 16.dp)) {
            item("playlist_heading") {
                QueueSectionHeading(stringResource(R.string.player_queue_count, state.queue.size))
            }
            if (state.queue.isEmpty()) item("empty_queue") {
                ZivLibraryText(stringResource(R.string.player_queue_empty), Modifier.padding(24.dp), secondary = true)
            }
            items(state.queue, key = { "queue_${it.index}" }) { item ->
                ZivLibraryRow(item.title,
                    if (item.selected) stringResource(R.string.player_queue_current) else "${item.index + 1}",
                    if (item.selected) ZivPlaybackIcon.PLAY else ZivPlaybackIcon.LIST,
                    { onSelect(item.index) },
                    Modifier.testTag("queue_${item.index}").semantics { selected = item.selected },
                    enabled = state.canSelectQueueItem)
            }
            item("history_heading") { QueueSectionHeading(stringResource(R.string.player_history_title)) }
            message?.let { item("history_message") { ZivStatusText(it, Modifier.padding(20.dp)) } }
            recentMediaItems(history, onOpenHistory) { actionItem = it }
        }
    }
    RecentMediaActions(actionItem, { actionItem = null }, onForgetHistory)
}

@Composable
private fun QueueSectionHeading(title: String) {
    ZivLibraryText(title, Modifier.padding(start = 24.dp, end = 24.dp, top = 20.dp, bottom = 8.dp)
        .semantics { heading() }, secondary = true)
}
