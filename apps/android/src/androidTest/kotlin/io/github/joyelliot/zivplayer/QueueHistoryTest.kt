// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.util.UUID

class QueueHistoryTest {
    @get:Rule val foreground = DeviceForegroundRule()

    @Test fun queuedIdentityIsHiddenStableAndPromotedOnlyWhenPlayed() = runBlocking {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as ZivPlayerApplication
        val repository = app.recentMediaRepository
        val registration = MediaRegistration(DurableMediaUri("content://io.github.joyelliot.zivplayer.test.fixtures/history-${UUID.randomUUID()}"),
            metadata = MediaMetadata(title = "Generated queue history test"), openedAt = EpochMilliseconds(1_000L), visibleInHistory = false)
        val record = repository.remember(registration)
        try {
            assertFalse(repository.observeRecentlyOpened(1000).first().any { it.record.id == record.id })
            val checkpoint = PlaybackCheckpoint(record.id, Milliseconds(2_000L), Milliseconds(10_000L), updatedAt = EpochMilliseconds(3_000L))
            assertTrue(repository.saveCheckpoint(checkpoint))
            assertEquals(record.id, repository.remember(registration.copy(openedAt = EpochMilliseconds(4_000L))).id)
            assertEquals(1_000L, repository.findById(record.id)?.record?.lastOpenedAt?.value)
            assertEquals(checkpoint, repository.findById(record.id)?.checkpoint)
            repository.markOpened(record.id, EpochMilliseconds(System.currentTimeMillis()))
            assertTrue(repository.observeRecentlyOpened(1000).first().any { it.record.id == record.id })
            assertEquals(checkpoint, repository.findById(record.id)?.checkpoint)
            val opened = checkNotNull(repository.findById(record.id)).record.lastOpenedAt
            repository.remember(registration.copy(openedAt = EpochMilliseconds(System.currentTimeMillis() + 1_000L)))
            assertEquals(opened, repository.findById(record.id)?.record?.lastOpenedAt)
            assertTrue(repository.observeRecentlyOpened(1000).first().any { it.record.id == record.id })
        } finally { repository.remove(record.id) }
    }
}
