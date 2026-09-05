// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
internal interface LibraryDao {
    @Query("SELECT * FROM library_folders ORDER BY name COLLATE NOCASE, folder_id")
    fun observeFolders(): Flow<List<LibraryFolderEntity>>
    @Query("SELECT * FROM library_media ORDER BY title COLLATE NOCASE, source_uri")
    fun observeMedia(): Flow<List<LibraryMediaEntity>>
    @Query("SELECT * FROM library_folders")
    suspend fun folders(): List<LibraryFolderEntity>
    @Query("SELECT * FROM library_folders WHERE folder_id = :id")
    suspend fun folder(id: String): LibraryFolderEntity?
    @Query("SELECT * FROM library_folders WHERE source_uri = :uri")
    suspend fun folderByUri(uri: String): LibraryFolderEntity?
    @Upsert suspend fun upsertFolder(folder: LibraryFolderEntity)
    @Query("DELETE FROM library_folders WHERE folder_id = :id")
    suspend fun deleteFolder(id: String)
    @Query("SELECT * FROM library_media WHERE folder_id = :id")
    suspend fun mediaForFolder(id: String): List<LibraryMediaEntity>
    @Query("DELETE FROM library_media WHERE folder_id = :id")
    suspend fun clearMedia(id: String)
    @Upsert suspend fun upsertMedia(media: List<LibraryMediaEntity>)
    @Query("UPDATE library_media SET favorite = :value WHERE folder_id = :folderId AND source_uri = :uri")
    suspend fun favorite(folderId: String, uri: String, value: Boolean)
    @Query("UPDATE library_media SET hidden = :value WHERE folder_id = :folderId AND source_uri = :uri")
    suspend fun hidden(folderId: String, uri: String, value: Boolean)
}
