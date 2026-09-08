// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.library

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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

private enum class Filter { FOLDERS, ALL, VIDEO, AUDIO, FAVORITES, HIDDEN }
private enum class Sort { NAME, MODIFIED, SIZE }
private enum class Menu { HOME, SORT, MANAGE, MEDIA, FOLDER }
private const val MAX_LIBRARY_QUEUE_ITEMS = 500
private data class LibrarySnapshot(val request: Any, val media: List<LibraryMedia>, val directories: List<LibraryDirectory>)

@Composable
fun LibraryScreen(
    folders: List<LibraryFolder>, media: List<LibraryMedia>,
    scanningFolderId: String?, scannedCount: Int, message: String?,
    onAddFolder: () -> Unit, onScan: (String) -> Unit, onCancelScan: () -> Unit,
    onRemoveFolder: (String) -> Unit, onOpen: (LibraryMedia) -> Unit,
    onFavorite: (LibraryMedia) -> Unit, onHidden: (LibraryMedia) -> Unit,
    onOpenQueue: (List<LibraryMedia>, Int) -> Unit = { items, index -> onOpen(items[index]) },
    onOpenFile: () -> Unit = {},
    onSettings: () -> Unit = {},
    onHistory: () -> Unit = {},
    onBrowseBackChanged: ((() -> Unit)?) -> Unit = {},
    foldersLoaded: Boolean = true,
    nowPlaying: @Composable () -> Unit = {},
) {
    var search by rememberSaveable { mutableStateOf("") }
    var searching by rememberSaveable { mutableStateOf(false) }
    var filter by rememberSaveable { mutableStateOf(Filter.FOLDERS) }
    var sort by rememberSaveable { mutableStateOf(Sort.NAME) }
    var selectedFolderId by rememberSaveable { mutableStateOf<String?>(null) }
    var selectedPath by rememberSaveable { mutableStateOf("") }
    var menu by remember { mutableStateOf<Menu?>(null) }
    var actionFolder by remember { mutableStateOf<LibraryFolder?>(null) }
    var actionMedia by remember { mutableStateOf<LibraryMedia?>(null) }
    val selectedFolder = folders.firstOrNull { it.id == selectedFolderId }
    val browseMedia = selectedFolderId != null || filter != Filter.FOLDERS
    fun backToFolders() { selectedFolderId = null; selectedPath = ""; search = ""; searching = false; filter = Filter.FOLDERS }
    fun browseBack() {
        if (searching) { search = ""; searching = false } else backToFolders()
    }
    DisposableEffect(selectedFolderId, filter, searching) {
        onBrowseBackChanged(if (selectedFolderId != null || filter != Filter.FOLDERS || searching) ({ browseBack() }) else null)
        onDispose { onBrowseBackChanged(null) }
    }
    LaunchedEffect(foldersLoaded, folders, selectedFolderId) {
        if (foldersLoaded && selectedFolderId != null && selectedFolder == null) backToFolders()
    }
    val request = remember(media, folders, search, filter, sort, selectedFolderId, selectedPath) { Any() }
    val projection by produceState<LibrarySnapshot?>(null, request) {
        value = withContext(Dispatchers.Default) {
            val query = search.trim()
            val rootNames = folders.associate { it.id to it.name }
            val selected = media.filter { item ->
                (selectedFolderId == null || (item.folderId == selectedFolderId && item.directoryPath() == selectedPath)) &&
                    (if (filter == Filter.HIDDEN) item.hidden else !item.hidden) &&
                    (query.isBlank() || item.title.contains(query, true) || item.relativePath.contains(query, true) || rootNames[item.folderId]?.contains(query, true) == true) &&
                    when (filter) {
                        Filter.VIDEO -> !MediaFileClassifier.isAudio(item.title, item.mimeType)
                        Filter.AUDIO -> MediaFileClassifier.isAudio(item.title, item.mimeType)
                        Filter.FAVORITES -> item.favorite
                        else -> true
                    }
            }
            val shown = when (sort) {
                Sort.NAME -> selected.sortedWith(compareBy<LibraryMedia, String>(String.CASE_INSENSITIVE_ORDER) { it.title }.thenBy { it.relativePath }.thenBy { it.sourceUri.value })
                Sort.MODIFIED -> selected.sortedWith(compareByDescending<LibraryMedia> { it.modifiedEpochMs ?: 0L }.thenBy { it.sourceUri.value })
                Sort.SIZE -> selected.sortedWith(compareByDescending<LibraryMedia> { it.sizeBytes ?: 0L }.thenBy { it.sourceUri.value })
            }
            val grouped = libraryDirectories(folders, shown)
            // Empty grants remain reachable, so failed or unscanned folders can be repaired.
            val indexedRoots = shown.mapTo(hashSetOf()) { it.folderId }
            val empty = folders.filter { it.id !in indexedRoots && (search.isBlank() || it.name.contains(search.trim(), true)) }
                .map { LibraryDirectory(it, "", emptyList()) }
            val all = grouped + empty
            val directories = when (sort) {
                Sort.NAME -> all.sortedWith(compareBy<LibraryDirectory, String>(String.CASE_INSENSITIVE_ORDER) { it.name }.thenBy { it.key })
                Sort.MODIFIED -> all.sortedWith(compareByDescending<LibraryDirectory> { it.media.maxOfOrNull { file -> file.modifiedEpochMs ?: 0L } ?: 0L }.thenBy { it.key })
                Sort.SIZE -> all.sortedWith(compareByDescending<LibraryDirectory> { it.media.sumOf { file -> file.sizeBytes ?: 0L } }.thenBy { it.key })
            }
            LibrarySnapshot(request, shown, directories)
        }
    }
    // A result belongs to exactly one filter/index snapshot. Old rows cannot be played
    // during background recomputation after a query, hidden flag or directory changes.
    val currentProjection = projection?.takeIf { it.request === request }
    val shown = currentProjection?.media.orEmpty()
    val directories = currentProjection?.directories.orEmpty()
    val loading = !foldersLoaded || currentProjection == null
    val listState = rememberLazyListState()
    val scrollKey = listOf(selectedFolderId.orEmpty(), selectedPath, filter.name, search, sort.name)
    var previousScrollKey by rememberSaveable { mutableStateOf(scrollKey) }
    LaunchedEffect(scrollKey) {
        if (previousScrollKey != scrollKey) { listState.scrollToItem(0); previousScrollKey = scrollKey }
    }
    val pageTitle = selectedFolder?.let { selectedPath.substringAfterLast('/').ifBlank { it.name } }
        ?: stringResource(filter.label())
    ZivLibrarySurface(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
        Row(Modifier.fillMaxWidth().padding(start = if (selectedFolderId == null) 20.dp else 4.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            if (selectedFolderId != null) ZivLibraryIconButton(ZivPlaybackIcon.BACK, stringResource(R.string.library_back_folders), ::browseBack)
            ZivLibraryText(pageTitle, Modifier.weight(1f), title = selectedFolderId == null, maxLines = 1)
            ZivLibraryIconButton(if (searching) ZivPlaybackIcon.CLOSE else ZivPlaybackIcon.SEARCH,
                stringResource(R.string.library_search), { searching = !searching; if (!searching) search = "" })
            ZivLibraryIconButton(ZivPlaybackIcon.SORT, stringResource(R.string.library_sort), { menu = Menu.SORT })
            ZivLibraryIconButton(ZivPlaybackIcon.MORE, stringResource(R.string.library_item_actions), { menu = Menu.HOME })
        }
        if (searching) ZivTextField(search, { search = it.take(200) }, stringResource(R.string.library_search), Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
        if (selectedFolderId == null) Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Filter.entries.filter { it != Filter.HIDDEN }.forEach { item ->
                ZivLibraryChip(stringResource(item.label()), filter == item) { filter = item }
            }
        }
        if (scanningFolderId != null) Row(Modifier.padding(horizontal = 20.dp), verticalAlignment = Alignment.CenterVertically) {
            ZivLibraryText(pluralStringResource(R.plurals.library_found, scannedCount, scannedCount), Modifier.weight(1f), secondary = true)
            ZivLibraryIconButton(ZivPlaybackIcon.CLOSE, stringResource(R.string.library_cancel), onCancelScan)
        }
        message?.let { ZivStatusText(it, Modifier.padding(horizontal = 20.dp, vertical = 8.dp)) }
        if (browseMedia) Row(Modifier.padding(start = 20.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            ZivLibraryText(pluralStringResource(R.plurals.library_items, shown.size, shown.size), Modifier.weight(1f), secondary = true)
            ZivLibraryIconButton(ZivPlaybackIcon.PLAY, stringResource(R.string.library_play_all), { onOpenQueue(shown.toList(), 0) }, enabled = shown.isNotEmpty())
        }
        if (loading) Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) { ZivLoadingIndicator() }
        else LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = listState, contentPadding = PaddingValues(bottom = 16.dp)) {
            if (!browseMedia) {
                items(directories, key = { it.key }) { directory ->
                    val files = directory.media
                    val audio = directory.audioCount
                    val count = mediaCountText(files.size - audio, audio)
                    val status = folderStatus(directory.root.status)
                    val location = if (directory.path.isEmpty()) null else "${directory.root.name}/${directory.path}"
                    ZivLibraryRow(directory.name, listOfNotNull(count, location, status).joinToString(" · "), ZivPlaybackIcon.FOLDER,
                        onClick = { selectedFolderId = directory.root.id; selectedPath = directory.path; search = ""; searching = false }) {
                        ZivLibraryIconButton(ZivPlaybackIcon.MORE, stringResource(R.string.library_folder_actions, directory.name), {
                            actionFolder = directory.root; menu = Menu.FOLDER
                        })
                    }
                }
                if (!loading && directories.isEmpty()) item {
                    Column(Modifier.fillMaxWidth().padding(32.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        ZivLibraryText(stringResource(if (folders.isEmpty()) R.string.library_folder_hint else R.string.library_empty), secondary = true)
                        if (folders.isEmpty()) ZivPrimaryButton(stringResource(R.string.library_add_folder), onAddFolder)
                    }
                }
            } else {
                items(shown, key = { "${it.folderId}:${it.sourceUri.value}" }) { item ->
                    val audio = MediaFileClassifier.isAudio(item.title, item.mimeType)
                    val detail = listOfNotNull(item.sizeBytes?.let(::formatLibrarySize), item.directoryPath().takeIf { it.isNotBlank() && selectedFolderId == null }).joinToString(" · ")
                    ZivLibraryRow(item.title, detail, if (audio) ZivPlaybackIcon.AUDIO else ZivPlaybackIcon.PLAY,
                        onClick = {
                            if (shown.size > MAX_LIBRARY_QUEUE_ITEMS) onOpen(item)
                            else onOpenQueue(shown.toList(), shown.indexOf(item))
                        }) {
                        ZivLibraryIconButton(ZivPlaybackIcon.MORE, stringResource(R.string.library_file_actions, item.title), { actionMedia = item; menu = Menu.MEDIA })
                    }
                }
                if (!loading && shown.isEmpty()) item { ZivLibraryText(stringResource(R.string.library_empty), Modifier.padding(24.dp), secondary = true) }
            }
        }
        nowPlaying()
    }
    menu?.let { active ->
        ZivLibraryDialog(when (active) {
            Menu.SORT -> stringResource(R.string.library_sort)
            Menu.MANAGE -> stringResource(R.string.library_manage_folders)
            Menu.FOLDER -> actionFolder?.name.orEmpty()
            Menu.MEDIA -> actionMedia?.title.orEmpty()
            Menu.HOME -> stringResource(R.string.library_title)
        }, { menu = null }) {
            when (active) {
                Menu.HOME -> {
                    ZivLibraryMenuItem(stringResource(R.string.library_open_file), { menu = null; onOpenFile() })
                    ZivLibraryMenuItem(stringResource(R.string.library_add_folder), { menu = null; onAddFolder() })
                    ZivLibraryMenuItem(stringResource(R.string.library_manage_folders), { menu = Menu.MANAGE })
                    ZivLibraryMenuItem(stringResource(R.string.library_history), { menu = null; onHistory() })
                    ZivLibraryMenuItem(stringResource(R.string.library_hidden), { menu = null; selectedFolderId = null; filter = Filter.HIDDEN })
                    ZivLibraryMenuItem(stringResource(R.string.library_settings), { menu = null; onSettings() })
                }
                Menu.SORT -> Sort.entries.forEach { item ->
                    ZivLibraryMenuItem(stringResource(when (item) {
                        Sort.NAME -> R.string.library_sort_name
                        Sort.MODIFIED -> R.string.library_sort_modified
                        Sort.SIZE -> R.string.library_sort_size
                    }), { sort = item; menu = null }, sort == item)
                }
                Menu.MANAGE -> {
                    folders.forEach { folder -> ZivLibraryMenuItem(folder.name, { actionFolder = folder; menu = Menu.FOLDER }) }
                    ZivLibraryMenuItem(stringResource(R.string.library_add_folder), { menu = null; onAddFolder() })
                }
                Menu.FOLDER -> actionFolder?.let { folder ->
                    ZivLibraryText(stringResource(R.string.library_source_scope, folder.name), Modifier.padding(horizontal = 24.dp, vertical = 8.dp), secondary = true)
                    folderStatus(folder.status)?.let { ZivLibraryText(it, Modifier.padding(horizontal = 24.dp), secondary = true) }
                    folder.message?.let { ZivLibraryText(it, Modifier.padding(horizontal = 24.dp), secondary = true) }
                    ZivLibraryMenuItem(stringResource(if (folder.id == scanningFolderId) R.string.library_cancel else R.string.library_rescan), {
                        menu = null; if (folder.id == scanningFolderId) onCancelScan() else onScan(folder.id)
                    })
                    ZivLibraryMenuItem(stringResource(R.string.library_remove), { menu = null; onRemoveFolder(folder.id) })
                }
                Menu.MEDIA -> actionMedia?.let { file ->
                    ZivLibraryMenuItem(stringResource(if (file.favorite) R.string.library_unfavorite else R.string.library_favorite), { menu = null; onFavorite(file) })
                    ZivLibraryMenuItem(stringResource(if (file.hidden) R.string.library_unhide else R.string.library_hide), { menu = null; onHidden(file) })
                }
            }
        }
    }
}

private fun Filter.label() = when (this) {
    Filter.FOLDERS -> R.string.library_folders_tab
    Filter.ALL -> R.string.library_all
    Filter.VIDEO -> R.string.library_video
    Filter.AUDIO -> R.string.library_audio
    Filter.FAVORITES -> R.string.library_favorites
    Filter.HIDDEN -> R.string.library_hidden
}

@Composable
private fun mediaCountText(video: Int, audio: Int): String = listOfNotNull(
    if (video > 0) pluralStringResource(R.plurals.library_videos, video, video) else null,
    if (audio > 0) pluralStringResource(R.plurals.library_audios, audio, audio) else null,
).joinToString(" · ").ifEmpty { pluralStringResource(R.plurals.library_items, 0, 0) }

@Composable
private fun folderStatus(status: LibraryFolderStatus): String? = when (status) {
    LibraryFolderStatus.READY -> null
    LibraryFolderStatus.NEVER_SCANNED -> stringResource(R.string.library_never_scanned)
    LibraryFolderStatus.SCANNING -> stringResource(R.string.library_scanning)
    LibraryFolderStatus.INTERRUPTED -> stringResource(R.string.library_interrupted)
    LibraryFolderStatus.ACCESS_LOST -> stringResource(R.string.library_access_lost)
    LibraryFolderStatus.FAILED -> stringResource(R.string.library_failed)
}

private fun formatLibrarySize(bytes: Long): String = when {
    bytes >= 1_073_741_824 -> "${bytes / 1_073_741_824}.${bytes % 1_073_741_824 * 10 / 1_073_741_824} GB"
    bytes >= 1_048_576 -> "${bytes / 1_048_576}.${bytes % 1_048_576 * 10 / 1_048_576} MB"
    else -> "${bytes / 1024} KB"
}
