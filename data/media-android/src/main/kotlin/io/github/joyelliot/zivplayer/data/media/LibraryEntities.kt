// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryFolderStatus
import io.github.joyelliot.zivplayer.core.media.LibraryMedia

@Entity(tableName = "library_folders", indices = [Index(value = ["source_uri"], unique = true)])
internal data class LibraryFolderEntity(
    @PrimaryKey @ColumnInfo(name = "folder_id") val id: String,
    @ColumnInfo(name = "source_uri") val sourceUri: String,
    val name: String,
    val status: String,
    @ColumnInfo(name = "last_scan_epoch_ms") val lastScanEpochMs: Long?,
    @ColumnInfo(name = "media_count") val mediaCount: Int,
    val message: String?,
    @ColumnInfo(name = "scan_token") val scanToken: String?,
) {
    fun model() = LibraryFolder(id, DurableMediaUri(sourceUri), name,
        LibraryFolderStatus.entries.find { it.name == status } ?: LibraryFolderStatus.FAILED,
        lastScanEpochMs, mediaCount, message)
}

@Entity(
    tableName = "library_media",
    primaryKeys = ["folder_id", "source_uri"],
    foreignKeys = [ForeignKey(entity = LibraryFolderEntity::class,
        parentColumns = ["folder_id"], childColumns = ["folder_id"], onDelete = ForeignKey.CASCADE)],
    indices = [Index(value = ["source_uri"])],
)
internal data class LibraryMediaEntity(
    @ColumnInfo(name = "folder_id") val folderId: String,
    @ColumnInfo(name = "source_uri") val sourceUri: String,
    val title: String,
    @ColumnInfo(name = "mime_type") val mimeType: String?,
    @ColumnInfo(name = "relative_path") val relativePath: String,
    @ColumnInfo(name = "size_bytes") val sizeBytes: Long?,
    @ColumnInfo(name = "modified_epoch_ms") val modifiedEpochMs: Long?,
    val favorite: Boolean,
    val hidden: Boolean,
) {
    fun model() = LibraryMedia(folderId, DurableMediaUri(sourceUri), title, mimeType, relativePath,
        sizeBytes, modifiedEpochMs, favorite, hidden)
}
