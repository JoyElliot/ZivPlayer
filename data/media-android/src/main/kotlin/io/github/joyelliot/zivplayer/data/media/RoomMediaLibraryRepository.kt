// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.withTransaction
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryFolderStatus
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.core.media.MediaLibraryRepository
import kotlinx.coroutines.flow.map
import java.util.UUID

class RoomMediaLibraryRepository internal constructor(private val database: ZivMediaDatabase) : MediaLibraryRepository {
    private val dao = database.libraryDao()
    override fun observeFolders() = dao.observeFolders().map { it.map(LibraryFolderEntity::model) }
    override fun observeMedia() = dao.observeMedia().map { it.map(LibraryMediaEntity::model) }
    override suspend fun setFavorite(folderId: String, sourceUri: DurableMediaUri, favorite: Boolean) =
        dao.favorite(folderId, sourceUri.value, favorite)
    override suspend fun setHidden(folderId: String, sourceUri: DurableMediaUri, hidden: Boolean) =
        dao.hidden(folderId, sourceUri.value, hidden)

    suspend fun folders(): List<LibraryFolder> = dao.folders().map(LibraryFolderEntity::model)
    suspend fun folder(id: String): LibraryFolder? = dao.folder(id)?.model()

    internal suspend fun add(uri: String, name: String): LibraryFolder = database.withTransaction {
        val existing = dao.folderByUri(uri)
        val row = existing?.copy(name = name) ?: LibraryFolderEntity(UUID.randomUUID().toString(), uri,
            name, LibraryFolderStatus.NEVER_SCANNED.name, null, 0, null, null)
        dao.upsertFolder(row)
        row.model()
    }

    internal suspend fun remove(id: String) = dao.deleteFolder(id)

    suspend fun markInterruptedScans() = database.withTransaction {
        dao.folders().filter { it.status == LibraryFolderStatus.SCANNING.name }.forEach {
            dao.upsertFolder(it.copy(status = LibraryFolderStatus.INTERRUPTED.name,
                scanToken = null, message = "上次扫描已中断，可重新扫描"))
        }
    }

    internal suspend fun beginScan(id: String): String? = database.withTransaction {
        val row = dao.folder(id) ?: return@withTransaction null
        val token = UUID.randomUUID().toString()
        dao.upsertFolder(row.copy(status = LibraryFolderStatus.SCANNING.name, scanToken = token, message = null))
        token
    }

    internal suspend fun finishScan(id: String, token: String, entries: List<LibraryMedia>,
        complete: Boolean, status: LibraryFolderStatus, message: String?): Boolean = database.withTransaction {
        val folder = dao.folder(id) ?: return@withTransaction false
        if (folder.scanToken != token) return@withTransaction false
        val old = dao.mediaForFolder(id).associateBy { it.sourceUri }
        val incoming = entries.distinctBy { it.sourceUri }.map { item ->
            val previous = old[item.sourceUri.value]
            LibraryMediaEntity(id, item.sourceUri.value, item.title, item.mimeType, item.relativePath,
                item.sizeBytes, item.modifiedEpochMs, previous?.favorite ?: false, previous?.hidden ?: false)
        }
        // An interrupted/partial scan must not erase files in directories which were never visited.
        if (complete) dao.clearMedia(id)
        dao.upsertMedia(incoming)
        val count = if (complete) incoming.size else (old.keys + incoming.map { it.sourceUri }).size
        dao.upsertFolder(folder.copy(status = status.name, scanToken = null, mediaCount = count,
            lastScanEpochMs = if (complete) System.currentTimeMillis() else folder.lastScanEpochMs,
            message = message))
        true
    }
}
