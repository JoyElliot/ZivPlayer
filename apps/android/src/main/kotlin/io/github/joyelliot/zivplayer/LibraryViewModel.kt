// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.data.media.LibraryScanProgress
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.launch

/** Scans have their own job; opening media never cancels a library index update. */
class LibraryViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as ZivPlayerApplication
    val folders = mutableStateOf<List<LibraryFolder>>(emptyList())
    val foldersLoaded = mutableStateOf(false)
    val media = mutableStateOf<List<LibraryMedia>>(emptyList())
    val progress = mutableStateOf<LibraryScanProgress?>(null)
    val message = mutableStateOf<String?>(null)
    private var scanJob: Job? = null
    private var scanFolderId: String? = null
    private var scanRequest = 0L
    private val scanUpdates = Channel<Pair<Long, LibraryScanProgress>>(Channel.CONFLATED)
    private val initialization = operation { app.mediaRepositories.library.markInterruptedScans() }

    init {
        viewModelScope.launch {
            for ((request, update) in scanUpdates) if (request == scanRequest) progress.value = update
        }
        viewModelScope.launch {
            app.mediaRepositories.library.observeFolders().catch { message.value = "无法读取媒体文件夹" }
                .collect { folders.value = it; foldersLoaded.value = true }
        }
        viewModelScope.launch {
            app.mediaRepositories.library.observeMedia().catch { message.value = "无法读取媒体库" }
                .collect { media.value = it }
        }
    }

    fun addFolder(selection: OpenMediaDocumentResult) = operation {
        val folder = app.documentTreeLibrary.addFolder(selection.uri, selection.resultFlags)
        scan(folder.id)
    }

    fun scan(folderId: String) {
        // One provider scan at a time. Switching folders first cancels the preceding scan.
        val request = ++scanRequest
        val previous = scanJob
        previous?.cancel()
        scanFolderId = folderId
        scanJob = viewModelScope.launch {
            previous?.join()
            initialization.join()
            if (request != scanRequest) return@launch
            progress.value = LibraryScanProgress(folderId, 0, 0)
            message.value = null
            try {
                app.documentTreeLibrary.scan(folderId) { update ->
                    // Scanners run on IO; publish Compose state on the main dispatcher.
                    scanUpdates.trySend(request to update)
                }
            } catch (failure: CancellationException) { throw failure
            } catch (failure: Exception) { message.value = failure.message ?: "扫描失败"
            } finally { if (request == scanRequest) { progress.value = null; scanFolderId = null } }
        }
    }

    fun cancelScan() {
        ++scanRequest
        scanJob?.cancel()
        scanFolderId = null
        progress.value = null
        message.value = "扫描已取消，已有索引会保留"
    }

    fun removeFolder(id: String) = operation {
        if (scanFolderId == id) {
            val pending = scanJob
            cancelScan()
            pending?.join()
        }
        val released = app.documentTreeLibrary.removeFolder(id)
        message.value = if (released) "已移除文件夹索引，原文件保留" else "已移除索引，部分访问授权未能释放"
    }

    fun setFavorite(item: LibraryMedia) = operation {
        app.mediaRepositories.library.setFavorite(item.folderId, item.sourceUri, !item.favorite)
    }
    fun setHidden(item: LibraryMedia) = operation {
        app.mediaRepositories.library.setHidden(item.folderId, item.sourceUri, !item.hidden)
    }
    fun clearMessage() { message.value = null }

    private fun operation(action: suspend () -> Unit): Job = viewModelScope.launch {
        try { action() } catch (failure: CancellationException) { throw failure
        } catch (failure: Exception) { message.value = failure.message ?: "媒体库操作失败" }
    }
}

internal class OpenMediaFolder : ActivityResultContract<Unit, OpenMediaDocumentResult?>() {
    override fun createIntent(context: Context, input: Unit) = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION or
            Intent.FLAG_GRANT_PREFIX_URI_PERMISSION)
    override fun parseResult(resultCode: Int, intent: Intent?): OpenMediaDocumentResult? {
        if (resultCode != android.app.Activity.RESULT_OK) return null
        return intent?.data?.let { OpenMediaDocumentResult(it, intent.flags) }
    }
}
