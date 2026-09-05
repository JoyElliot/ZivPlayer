// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.library

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLocale
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryFolderStatus
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.core.media.MediaFileClassifier
import io.github.joyelliot.zivplayer.designsystem.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

private enum class Filter { ALL, VIDEO, AUDIO, FAVORITES, HIDDEN }
private enum class Sort { NAME, MODIFIED, SIZE }

@Composable
fun LibraryScreen(
    folders: List<LibraryFolder>, media: List<LibraryMedia>,
    scanningFolderId: String?, scannedCount: Int, message: String?,
    onAddFolder: () -> Unit, onScan: (String) -> Unit, onCancelScan: () -> Unit,
    onRemoveFolder: (String) -> Unit, onOpen: (LibraryMedia) -> Unit,
    onFavorite: (LibraryMedia) -> Unit, onHidden: (LibraryMedia) -> Unit,
) {
    val locale = LocalLocale.current.platformLocale
    var search by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableStateOf(Filter.ALL) }
    var sort by rememberSaveable { mutableStateOf(Sort.NAME) }
    var showFolders by rememberSaveable { mutableStateOf(true) }
    val shown by produceState(emptyList<LibraryMedia>(), media, search, filter, sort) {
        value = withContext(Dispatchers.Default) {
            val query = search.trim()
            val selected = media.filter { item ->
                (if (filter == Filter.HIDDEN) item.hidden else !item.hidden) &&
                    (query.isBlank() || item.title.contains(query, true) || item.relativePath.contains(query, true)) &&
                    when (filter) {
                        Filter.VIDEO -> !MediaFileClassifier.isAudio(item.title, item.mimeType)
                        Filter.AUDIO -> MediaFileClassifier.isAudio(item.title, item.mimeType)
                        Filter.FAVORITES -> item.favorite
                        else -> true
                    }
            }
            when (sort) {
                Sort.NAME -> selected.sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.title })
                Sort.MODIFIED -> selected.sortedByDescending { it.modifiedEpochMs ?: 0L }
                Sort.SIZE -> selected.sortedByDescending { it.sizeBytes ?: 0L }
            }
        }
    }
    ZivListScreen(stringResource(R.string.library_title)) { modifier ->
        LazyColumn(modifier = modifier, contentPadding = PaddingValues(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ZivPrimaryButton(stringResource(R.string.library_add_folder), onAddFolder, Modifier.weight(1f))
                    ZivPrimaryButton(stringResource(R.string.library_folders, folders.size),
                        { showFolders = !showFolders }, Modifier.weight(1f))
                }
            }
            if (showFolders) {
                if (folders.isEmpty()) item { ZivText(stringResource(R.string.library_folder_hint)) }
                items(folders, key = { "folder:${it.id}" }) { folder ->
                    ZivCard {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            ZivText(folder.name)
                            val status = when (folder.status) {
                                LibraryFolderStatus.NEVER_SCANNED -> R.string.library_never_scanned
                                LibraryFolderStatus.SCANNING -> R.string.library_scanning
                                LibraryFolderStatus.READY -> R.string.library_ready
                                LibraryFolderStatus.INTERRUPTED -> R.string.library_interrupted
                                LibraryFolderStatus.ACCESS_LOST -> R.string.library_access_lost
                                LibraryFolderStatus.FAILED -> R.string.library_failed
                            }
                            ZivText(stringResource(R.string.library_folder_status, stringResource(status),
                                pluralStringResource(R.plurals.library_items, folder.mediaCount, folder.mediaCount)))
                            folder.message?.let { ZivStatusText(it) }
                            if (scanningFolderId == folder.id) {
                                ZivText(pluralStringResource(R.plurals.library_found, scannedCount, scannedCount))
                                ZivPrimaryButton(stringResource(R.string.library_cancel), onCancelScan)
                            } else {
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    ZivPrimaryButton(stringResource(R.string.library_rescan), { onScan(folder.id) }, Modifier.weight(1f))
                                    ZivPrimaryButton(stringResource(R.string.library_remove), { onRemoveFolder(folder.id) }, Modifier.weight(1f))
                                }
                            }
                        }
                    }
                }
            }
            message?.let { item { ZivStatusText(it) } }
            item { ZivTextField(search, { search = it.take(200) }, stringResource(R.string.library_search)) }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ZivPrimaryButton(stringResource(when (filter) {
                        Filter.ALL -> R.string.library_all
                        Filter.VIDEO -> R.string.library_video
                        Filter.AUDIO -> R.string.library_audio
                        Filter.FAVORITES -> R.string.library_favorites
                        Filter.HIDDEN -> R.string.library_hidden
                    }), { filter = Filter.entries[(filter.ordinal + 1) % Filter.entries.size] }, Modifier.weight(1f))
                    ZivPrimaryButton(stringResource(when (sort) {
                        Sort.NAME -> R.string.library_sort_name
                        Sort.MODIFIED -> R.string.library_sort_modified
                        Sort.SIZE -> R.string.library_sort_size
                    }), { sort = Sort.entries[(sort.ordinal + 1) % Sort.entries.size] }, Modifier.weight(1f))
                }
            }
            item { ZivText(pluralStringResource(R.plurals.library_results, shown.size, shown.size)) }
            items(shown, key = { "media:${it.folderId}:${it.sourceUri.value}" }) { item ->
                ZivCard(onClick = { onOpen(item) }) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        ZivText(item.title)
                        ZivText(item.relativePath)
                        val info = listOfNotNull(item.sizeBytes?.let { String.format(locale, "%.1f MiB", it / 1048576.0) },
                            item.modifiedEpochMs?.takeIf { it > 0 }?.let { DateFormat.getDateInstance(DateFormat.DEFAULT, locale).format(Date(it)) })
                        if (info.isNotEmpty()) ZivText(info.joinToString(" · "))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            ZivPrimaryButton(stringResource(if (item.favorite) R.string.library_unfavorite else R.string.library_favorite),
                                { onFavorite(item) }, Modifier.weight(1f))
                            ZivPrimaryButton(stringResource(if (item.hidden) R.string.library_unhide else R.string.library_hide),
                                { onHidden(item) }, Modifier.weight(1f))
                        }
                    }
                }
            }
            if (shown.isEmpty()) item { ZivText(stringResource(R.string.library_empty)) }
        }
    }
}
