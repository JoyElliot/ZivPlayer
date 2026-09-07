// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.MediaRecord
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.media.RecentMedia
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaDocumentRegistrationCoordinatorTest {
    @Test
    fun `library queue uses existing identity without publishing history or taking new grants`() = runBlocking {
        val repository = FakeRepository()
        val access = FakeDocumentAccess(PersistedReadGrant.PREEXISTING)
        val opened = coordinator(repository, access).open(SOURCE_URI, persistableReadOffered = false, visibleInHistory = false)
        assertEquals(STABLE_ID, opened.mediaId)
        assertFalse(repository.remembered.single().visibleInHistory)
        assertEquals(listOf(false), access.takePolicy)
        assertTrue(access.released.isEmpty())
    }

    @Test
    fun `persisted selection uses repository identity and metadata`() = runBlocking {
        val repository = FakeRepository()
        val access = FakeDocumentAccess(PersistedReadGrant.ACQUIRED)
        val coordinator = coordinator(repository, access)

        val opened = coordinator.open(SOURCE_URI)

        assertEquals(STABLE_ID, opened.mediaId)
        assertEquals(MediaDocumentPersistence.PERSISTED, opened.persistence)
        assertTrue(opened.isRestartSafe)
        assertEquals("video/mp4", opened.mimeType)
        assertEquals("Example.mp4", opened.metadata.title)
        assertEquals(SOURCE_URI, repository.remembered.single().sourceUri.value)
        assertTrue(access.released.isEmpty())
    }

    @Test
    fun `unavailable grant plays session-only without writing history`() = runBlocking {
        val repository = FakeRepository()
        val access = FakeDocumentAccess(PersistedReadGrant.UNAVAILABLE)
        val coordinator = coordinator(repository, access)

        val opened = coordinator.open(SOURCE_URI)

        assertEquals(SESSION_ID, opened.mediaId)
        assertEquals(
            MediaDocumentPersistence.SESSION_ONLY_PERMISSION_UNAVAILABLE,
            opened.persistence,
        )
        assertFalse(opened.isRestartSafe)
        assertTrue(repository.remembered.isEmpty())
        assertTrue(access.released.isEmpty())
    }

    @Test
    fun `missing result grant flags do not attempt to take a new grant`() = runBlocking {
        val repository = FakeRepository()
        val access = FakeDocumentAccess(PersistedReadGrant.ACQUIRED)
        val coordinator = coordinator(repository, access)

        val opened = coordinator.open(SOURCE_URI, persistableReadOffered = false)

        assertEquals(
            MediaDocumentPersistence.SESSION_ONLY_PERMISSION_UNAVAILABLE,
            opened.persistence,
        )
        assertEquals(listOf(false), access.takePolicy)
        assertTrue(repository.remembered.isEmpty())
    }

    @Test
    fun `preexisting grant remains durable when result omits grant flags`() = runBlocking {
        val repository = FakeRepository()
        val access = FakeDocumentAccess(PersistedReadGrant.PREEXISTING)
        val coordinator = coordinator(repository, access)

        val opened = coordinator.open(SOURCE_URI, persistableReadOffered = false)

        assertEquals(MediaDocumentPersistence.PERSISTED, opened.persistence)
        assertEquals(STABLE_ID, opened.mediaId)
        assertEquals(listOf(false), access.takePolicy)
    }

    @Test
    fun `failed history write releases a newly acquired grant`() = runBlocking {
        val repository = FakeRepository(failRemember = true)
        val access = FakeDocumentAccess(PersistedReadGrant.ACQUIRED)
        val coordinator = coordinator(repository, access)

        val opened = coordinator.open(SOURCE_URI)

        assertEquals(SESSION_ID, opened.mediaId)
        assertEquals(
            MediaDocumentPersistence.SESSION_ONLY_HISTORY_WRITE_FAILED,
            opened.persistence,
        )
        assertEquals(listOf(SOURCE_URI), access.released)
    }

    @Test
    fun `forget removes history before releasing its grant`() = runBlocking {
        val repository = FakeRepository(existing = storedRecent())
        val access = FakeDocumentAccess(PersistedReadGrant.PREEXISTING)
        val coordinator = coordinator(repository, access)

        assertEquals(MediaDocumentForgetResult.REMOVED, coordinator.forget(STABLE_ID))
        assertEquals(listOf(STABLE_ID), repository.removed)
        assertEquals(listOf(SOURCE_URI), access.released)
    }

    @Test
    fun `forget reports an orphan when provider grant release fails`() = runBlocking {
        val repository = FakeRepository(existing = storedRecent())
        val access = FakeDocumentAccess(
            grant = PersistedReadGrant.PREEXISTING,
            releaseSucceeds = false,
        )
        val coordinator = coordinator(repository, access)

        assertEquals(
            MediaDocumentForgetResult.REMOVED_WITH_ORPHANED_GRANT,
            coordinator.forget(STABLE_ID),
        )
    }

    private fun coordinator(
        repository: FakeRepository,
        access: FakeDocumentAccess,
    ): MediaDocumentRegistrationCoordinator = MediaDocumentRegistrationCoordinator(
        repository = repository,
        documentAccess = access,
        clock = { OPENED_AT },
        sessionIdFactory = { SESSION_ID },
    )

    private fun storedRecent(): RecentMedia = RecentMedia(
        record = MediaRecord(
            id = STABLE_ID,
            sourceUri = DurableMediaUri(SOURCE_URI),
            mimeType = "video/mp4",
            metadata = MediaMetadata(title = "Example.mp4"),
            addedAt = EpochMilliseconds(OPENED_AT),
            lastOpenedAt = EpochMilliseconds(OPENED_AT),
        ),
    )

    private class FakeDocumentAccess(
        private val grant: PersistedReadGrant,
        private val releaseSucceeds: Boolean = true,
    ) : DocumentAccess {
        val released = mutableListOf<String>()
        val takePolicy = mutableListOf<Boolean>()

        override fun acquirePersistedReadGrant(
            sourceUri: String,
            mayTakeNewGrant: Boolean,
        ): PersistedReadGrant {
            takePolicy += mayTakeNewGrant
            return if (mayTakeNewGrant || grant == PersistedReadGrant.PREEXISTING) {
                grant
            } else {
                PersistedReadGrant.UNAVAILABLE
            }
        }

        override fun releasePersistedReadGrant(sourceUri: String): Boolean {
            released += sourceUri
            return releaseSucceeds
        }

        override fun readDetails(sourceUri: String): MediaDocumentDetails = MediaDocumentDetails(
            mimeType = "video/mp4",
            metadata = MediaMetadata(title = "Example.mp4"),
        )
    }

    private class FakeRepository(
        private var existing: RecentMedia? = null,
        private val failRemember: Boolean = false,
    ) : RecentMediaRepository {
        val remembered = mutableListOf<MediaRegistration>()
        val removed = mutableListOf<MediaId>()

        override fun observeRecentlyOpened(limit: Int): Flow<List<RecentMedia>> = emptyFlow()

        override suspend fun findById(id: MediaId): RecentMedia? = existing?.takeIf {
            it.record.id == id
        }

        override suspend fun findBySourceUri(sourceUri: DurableMediaUri): RecentMedia? =
            existing?.takeIf { it.record.sourceUri == sourceUri }

        override suspend fun remember(registration: MediaRegistration): MediaRecord {
            remembered += registration
            if (failRemember) {
                error("database unavailable")
            }
            return MediaRecord(
                id = STABLE_ID,
                sourceUri = registration.sourceUri,
                mimeType = registration.mimeType,
                metadata = registration.metadata,
                addedAt = registration.openedAt,
                lastOpenedAt = registration.openedAt,
            )
        }

        override suspend fun saveCheckpoint(checkpoint: PlaybackCheckpoint): Boolean = false

        override suspend fun remove(id: MediaId): Boolean {
            removed += id
            existing = null
            return true
        }
    }

    private companion object {
        const val SOURCE_URI = "content://provider/document/42"
        const val OPENED_AT = 1_000L
        val STABLE_ID = MediaId("stable-id")
        val SESSION_ID = MediaId("session-id")
    }
}
