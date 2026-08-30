// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.MediaId
import kotlinx.coroutines.flow.Flow

/**
 * Local history for media explicitly opened by the user.
 *
 * Implementations assign an opaque stable [MediaId] on first registration and reuse it when the
 * same [DurableMediaUri] is registered again. Queue item identifiers are never used as media IDs.
 */
interface RecentMediaRepository {
    /** Observes media ordered by [MediaRecord.lastOpenedAt], newest first. */
    fun observeRecentlyOpened(limit: Int): Flow<List<RecentMedia>>

    suspend fun findById(id: MediaId): RecentMedia?

    suspend fun findBySourceUri(sourceUri: DurableMediaUri): RecentMedia?

    suspend fun remember(registration: MediaRegistration): MediaRecord

    /** Returns false when the media record no longer exists. */
    suspend fun saveCheckpoint(checkpoint: PlaybackCheckpoint): Boolean

    /** Returns false when the media record no longer exists. */
    suspend fun remove(id: MediaId): Boolean
}
