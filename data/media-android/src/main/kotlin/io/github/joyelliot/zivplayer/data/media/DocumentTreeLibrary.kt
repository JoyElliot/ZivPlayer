// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.CancellationSignal
import android.provider.DocumentsContract
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryFolderStatus
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.core.media.MediaFileClassifier
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import java.io.IOException

data class LibraryScanProgress(val folderId: String, val mediaFound: Int, val directoriesVisited: Int)

class DocumentTreeLibrary(context: Context, private val repositories: MediaRepositories) {
    private val resolver = context.applicationContext.contentResolver
    private val library = repositories.library
    private val scanGate = Mutex()
    private val providerQueries = BoundedBlockingIo("ZivLibraryProvider")

    suspend fun addFolder(uri: Uri, resultFlags: Int): LibraryFolder = withContext(Dispatchers.IO) {
        repositories.documentOperations.withLock {
            withContext(NonCancellable) {
                require(uri.scheme == ContentResolver.SCHEME_CONTENT && DocumentsContract.isTreeUri(uri)) {
                    "请选择可访问的媒体文件夹"
                }
                val existing = resolver.hasPersistedDocumentRead(uri)
                if (!existing) {
                    val offered = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                    require(resultFlags and offered == offered) { "文件提供者未提供持久读取授权" }
                    resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                check(resolver.hasPersistedDocumentRead(uri)) { "未能保存文件夹访问授权" }
                try {
                    val root = DocumentsContract.buildDocumentUriUsingTree(uri, DocumentsContract.getTreeDocumentId(uri))
                    val name = withTimeout(QUERY_TIMEOUT_MS) { providerQueries.run { _ ->
                        resolver.query(root, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME),
                            null, null, null)?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
                    } }
                        ?.takeIf { it.isNotBlank() }?.take(512) ?: "媒体文件夹"
                    library.add(uri.toString(), name)
                } catch (failure: Exception) {
                    if (!existing) runCatching { resolver.releasePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
                    throw failure
                }
            }
        }
    }

    /** Removes only the index. A tree grant still needed by history is retained. */
    suspend fun removeFolder(id: String): Boolean = withContext(Dispatchers.IO) {
        scanGate.withLock {
        repositories.documentOperations.withLock {
            withContext(NonCancellable) removal@{
                val folder = library.folder(id) ?: return@removal true
                library.remove(id)
                releaseUnusedTreeGrant(Uri.parse(folder.sourceUri.value))
            }
        }
        }
    }

    suspend fun releaseUnusedTreeGrants(): Int = withContext(Dispatchers.IO) {
        repositories.documentOperations.withLock {
            var retainedFailures = 0
            resolver.persistedUriPermissions.filter { it.isReadPermission && DocumentsContract.isTreeUri(it.uri) }
                .forEach { if (!releaseUnusedTreeGrant(it.uri)) retainedFailures++ }
            retainedFailures
        }
    }

    private suspend fun releaseUnusedTreeGrant(grant: Uri): Boolean {
        if (library.folders().any { grantCoversDocument(grant, Uri.parse(it.sourceUri.value)) }) return true
        if (repositories.database.recentMediaDao().allSourceUris().any { grantCoversDocument(grant, Uri.parse(it)) }) return true
        return runCatching {
            resolver.releasePersistableUriPermission(grant, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            resolver.persistedUriPermissions.none { it.uri == grant && it.isReadPermission }
        }.getOrDefault(false)
    }

    suspend fun scan(id: String, onProgress: (LibraryScanProgress) -> Unit = {}) = withContext(Dispatchers.IO) {
        scanGate.withLock {
            val folder = library.folder(id) ?: return@withLock
            val token = withContext(NonCancellable) { library.beginScan(id) } ?: return@withLock
            val entries = ArrayList<LibraryMedia>()
            var status = LibraryFolderStatus.READY
            var message: String? = null
            var complete = false
            try {
                val tree = Uri.parse(folder.sourceUri.value)
                if (!resolver.hasPersistedDocumentRead(tree)) throw SecurityException("文件夹访问授权已失效")
                val pending = ArrayDeque<Pair<String, String>>()
                val visited = HashSet<String>()
                pending.add(DocumentsContract.getTreeDocumentId(tree) to "")
                var nodesSeen = 0
                while (pending.isNotEmpty()) {
                    currentCoroutineContext().ensureActive()
                    val (documentId, path) = pending.removeFirst()
                    if (!visited.add(documentId)) continue
                    if (visited.size > MAX_DIRECTORIES || nodesSeen >= MAX_DOCUMENTS) throw ScanLimitReached()
                    val children = withTimeout(QUERY_TIMEOUT_MS) {
                        queryChildren(tree, documentId, (MAX_DOCUMENTS - nodesSeen).coerceAtMost(MAX_CHILDREN))
                    }
                    nodesSeen += children.size
                    for (child in children) {
                        currentCoroutineContext().ensureActive()
                        val relativePath = if (path.isEmpty()) child.name else "$path/${child.name}"
                        if (relativePath.length > 4096) throw ScanLimitReached()
                        if (child.mimeType == DocumentsContract.Document.MIME_TYPE_DIR) {
                            if (pending.size + visited.size >= MAX_DIRECTORIES) throw ScanLimitReached()
                            pending.add(child.id to relativePath)
                        } else if (MediaFileClassifier.isMedia(child.name, child.mimeType)) {
                            if (entries.size >= MAX_MEDIA) throw ScanLimitReached()
                            entries.add(LibraryMedia(id, DurableMediaUri(
                                DocumentsContract.buildDocumentUriUsingTree(tree, child.id).toString()),
                                child.name, child.mimeType, relativePath, child.size, child.modified))
                        }
                    }
                    onProgress(LibraryScanProgress(id, entries.size, visited.size))
                }
                complete = true
            } catch (_: TimeoutCancellationException) {
                status = LibraryFolderStatus.FAILED
                message = "文件提供者响应超时，原索引保留；请稍后重新扫描"
            } catch (cancelled: CancellationException) {
                withContext(NonCancellable) {
                    library.finishScan(id, token, entries, false, LibraryFolderStatus.INTERRUPTED, "扫描已取消，已发现的媒体保留")
                }
                throw cancelled
            } catch (_: SecurityException) {
                status = LibraryFolderStatus.ACCESS_LOST
                message = "文件夹访问授权已失效，请重新添加文件夹"
            } catch (_: ScanLimitReached) {
                status = LibraryFolderStatus.FAILED
                message = "文件夹规模超过单次扫描上限，请选择较小的子文件夹；原索引保留"
            } catch (_: Exception) {
                status = LibraryFolderStatus.FAILED
                message = "提供者未能完成扫描，请检查文件夹后重试；原索引保留"
            }
            withContext(NonCancellable) { library.finishScan(id, token, entries, complete, status, message) }
        }
    }

    private suspend fun queryChildren(tree: Uri, parentId: String, limit: Int): List<DocumentNode> {
        val cancellation = CancellationSignal()
        return providerQueries.run(onCancel = { cancellation.cancel() }) { isActive ->
                val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, parentId)
                val projection = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_MIME_TYPE,
                    DocumentsContract.Document.COLUMN_SIZE, DocumentsContract.Document.COLUMN_LAST_MODIFIED)
                val result = resolver.query(children, projection, null, null, null, cancellation)?.use { cursor ->
                    val items = ArrayList<DocumentNode>()
                    while (cursor.moveToNext()) {
                        if (!isActive()) throw CancellationException("Scan cancelled")
                        if (items.size >= limit) throw ScanLimitReached()
                        val id = cursor.getString(0) ?: throw IOException("Missing document identity")
                        if (id.length > 4096) throw ScanLimitReached()
                        val name = cursor.getString(1)?.takeIf { it.isNotBlank() }?.take(512) ?: "未命名媒体"
                        items.add(DocumentNode(id, name, cursor.getString(2),
                            if (cursor.isNull(3)) null else cursor.getLong(3).takeIf { it >= 0 },
                            if (cursor.isNull(4)) null else cursor.getLong(4).takeIf { it >= 0 }))
                    }
                    items
                } ?: throw IOException("Provider returned no cursor")
                result
        }
    }

    private data class DocumentNode(val id: String, val name: String, val mimeType: String?, val size: Long?, val modified: Long?)
    private class ScanLimitReached : IOException()
    private companion object {
        const val MAX_DIRECTORIES = 5_000
        const val MAX_DOCUMENTS = 100_000
        const val MAX_CHILDREN = 20_000
        const val MAX_MEDIA = 50_000
        const val QUERY_TIMEOUT_MS = 15_000L
    }
}
