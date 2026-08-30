// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.Context
import androidx.room.Room
import androidx.room.withTransaction
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.MediaRecord
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.media.RecentMedia
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.core.model.MediaId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.io.Closeable
import java.util.UUID

class RoomRecentMediaRepository private constructor(
    private val database: ZivMediaDatabase,
    private val idFactory: () -> MediaId,
) : RecentMediaRepository, Closeable {
    private val dao = database.recentMediaDao()

    override fun observeRecentlyOpened(limit: Int): Flow<List<RecentMedia>> {
        require(limit > 0) { "Recent media limit must be positive." }
        return dao.observeRecentOpened(limit).map { entities ->
            entities.map(RecentMediaEntity::toRecentMedia)
        }
    }

    override suspend fun findById(id: MediaId): RecentMedia? =
        dao.findById(id.value)?.toRecentMedia()

    override suspend fun findBySourceUri(sourceUri: DurableMediaUri): RecentMedia? =
        dao.findBySourceUri(sourceUri.value)?.toRecentMedia()

    override suspend fun remember(registration: MediaRegistration): MediaRecord =
        database.withTransaction {
            val stored = dao.findBySourceUri(registration.sourceUri.value)
                ?.merge(registration)
                ?: registration.toNewEntity(idFactory())
            dao.upsert(stored)
            stored.toRecentMedia().record
        }

    override suspend fun saveCheckpoint(checkpoint: PlaybackCheckpoint): Boolean =
        database.withTransaction {
            val stored = dao.findById(checkpoint.mediaId.value) ?: return@withTransaction false
            dao.upsert(stored.withCheckpoint(checkpoint))
            true
        }

    override suspend fun remove(id: MediaId): Boolean = dao.deleteById(id.value) > 0

    override fun close() {
        database.close()
    }

    companion object {
        private const val DATABASE_NAME = "zivplayer-media.db"

        fun create(context: Context): RoomRecentMediaRepository = RoomRecentMediaRepository(
            database = Room.databaseBuilder(
                context.applicationContext,
                ZivMediaDatabase::class.java,
                DATABASE_NAME,
            ).build(),
            idFactory = { MediaId(UUID.randomUUID().toString()) },
        )
    }
}
