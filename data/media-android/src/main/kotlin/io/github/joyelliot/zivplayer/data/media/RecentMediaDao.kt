// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
internal interface RecentMediaDao {
    @Query(
        """
        SELECT * FROM recent_media
        WHERE visible_in_history = 1
        ORDER BY last_opened_at_epoch_ms DESC, media_id ASC
        LIMIT :limit
        """,
    )
    fun observeRecentOpened(limit: Int): Flow<List<RecentMediaEntity>>

    @Query("SELECT * FROM recent_media WHERE media_id = :mediaId LIMIT 1")
    suspend fun findById(mediaId: String): RecentMediaEntity?

    @Query("SELECT * FROM recent_media WHERE source_uri = :sourceUri LIMIT 1")
    suspend fun findBySourceUri(sourceUri: String): RecentMediaEntity?

    @Query("SELECT source_uri FROM recent_media")
    suspend fun allSourceUris(): List<String>

    @Query("UPDATE recent_media SET visible_in_history = 1, last_opened_at_epoch_ms = :openedAt WHERE media_id = :mediaId")
    suspend fun markOpened(mediaId: String, openedAt: Long): Int

    @Upsert
    suspend fun upsert(entity: RecentMediaEntity)

    @Query("DELETE FROM recent_media WHERE media_id = :mediaId")
    suspend fun deleteById(mediaId: String): Int
}
