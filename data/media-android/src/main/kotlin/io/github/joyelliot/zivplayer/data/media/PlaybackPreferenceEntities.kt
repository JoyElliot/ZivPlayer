// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert
import io.github.joyelliot.zivplayer.core.media.PlaybackResource
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.media.SavedTrackSelection
import io.github.joyelliot.zivplayer.core.model.PlaybackResourceId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "media_track_choices", primaryKeys = ["media_id", "kind"],
    foreignKeys = [ForeignKey(entity = RecentMediaEntity::class,
        parentColumns = ["media_id"], childColumns = ["media_id"], onDelete = ForeignKey.CASCADE)])
internal data class TrackChoiceEntity(
    @ColumnInfo(name = "media_id") val mediaId: String,
    val kind: String,
    @ColumnInfo(name = "track_index") val index: Int?,
    val language: String?,
    val label: String?,
    val codec: String?,
    val external: Boolean,
    val disabled: Boolean,
) {
    fun selection(): SavedTrackSelection? {
        val type = TrackKind.entries.find { it.name == kind } ?: return null
        return index?.takeIf { it >= 0 }?.let {
            SavedTrackSelection(type, it, language, label, codec, external)
        }
    }
}

@Entity(tableName = "media_external_subtitles", primaryKeys = ["media_id", "resource_id"],
    foreignKeys = [ForeignKey(entity = RecentMediaEntity::class,
        parentColumns = ["media_id"], childColumns = ["media_id"], onDelete = ForeignKey.CASCADE)])
internal data class ExternalSubtitleEntity(
    @ColumnInfo(name = "media_id") val mediaId: String,
    @ColumnInfo(name = "resource_id") val resourceId: String,
    val title: String,
)

@Entity(tableName = "playback_resources")
internal data class PlaybackResourceEntity(
    @PrimaryKey @ColumnInfo(name = "resource_id") val id: String,
    val kind: String,
    val title: String,
    val extension: String,
    @ColumnInfo(name = "size_bytes") val sizeBytes: Long,
    @ColumnInfo(name = "font_family") val fontFamily: String?,
) {
    fun model() = PlaybackResource(PlaybackResourceId(id), PlaybackResourceKind.valueOf(kind),
        title, extension, sizeBytes, fontFamily)
}

@Dao
internal interface PlaybackPreferencesDao {
    @Query("SELECT * FROM media_track_choices WHERE media_id = :mediaId")
    suspend fun choices(mediaId: String): List<TrackChoiceEntity>
    @Upsert suspend fun upsertChoice(choice: TrackChoiceEntity)
    @Query("DELETE FROM media_track_choices WHERE media_id = :mediaId")
    suspend fun clearChoices(mediaId: String)
    @Query("DELETE FROM media_track_choices WHERE media_id = :mediaId AND kind = :kind")
    suspend fun deleteChoice(mediaId: String, kind: String)
    @Query("SELECT * FROM media_external_subtitles WHERE media_id = :mediaId ORDER BY resource_id")
    suspend fun subtitles(mediaId: String): List<ExternalSubtitleEntity>
    @Upsert suspend fun upsertSubtitle(subtitle: ExternalSubtitleEntity)
    @Query("DELETE FROM media_external_subtitles WHERE media_id = :mediaId")
    suspend fun clearSubtitles(mediaId: String)
    @Query("DELETE FROM media_external_subtitles WHERE resource_id = :resourceId")
    suspend fun forgetResource(resourceId: String)
}

@Dao
internal interface PlaybackResourcesDao {
    @Query("SELECT * FROM playback_resources ORDER BY kind, title COLLATE NOCASE, resource_id")
    fun observe(): Flow<List<PlaybackResourceEntity>>
    @Query("SELECT * FROM playback_resources")
    suspend fun all(): List<PlaybackResourceEntity>
    @Query("SELECT * FROM playback_resources WHERE resource_id = :id")
    suspend fun find(id: String): PlaybackResourceEntity?
    @Upsert suspend fun upsert(resource: PlaybackResourceEntity)
    @Query("DELETE FROM playback_resources WHERE resource_id = :id")
    suspend fun delete(id: String)
}
