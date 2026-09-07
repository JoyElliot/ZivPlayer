// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RecentMediaEntityTest {
    @Test
    fun `queue registration stays hidden until it is actually opened`() {
        val hidden = REGISTRATION.copy(visibleInHistory = false).toNewEntity(ID)
        assertEquals(false, hidden.visibleInHistory)
        val promoted = hidden.merge(REGISTRATION.copy(openedAt = EpochMilliseconds(5_000L)))
        assertEquals(true, promoted.visibleInHistory)
        assertEquals(5_000L, promoted.lastOpenedAtEpochMs)
        assertEquals(hidden.addedAtEpochMs, promoted.addedAtEpochMs)
    }

    @Test
    fun `queue registration cannot move or hide already watched history`() {
        val existing = REGISTRATION.toNewEntity(ID).withCheckpoint(CHECKPOINT)
        val queued = existing.merge(REGISTRATION.copy(visibleInHistory = false, openedAt = EpochMilliseconds(5_000L)))
        assertEquals(existing, queued)
    }

    @Test
    fun `reopening a source preserves stable identity and checkpoint`() {
        val original = REGISTRATION.toNewEntity(ID).withCheckpoint(CHECKPOINT)
        val reopened = original.merge(
            MediaRegistration(
                sourceUri = URI,
                metadata = MediaMetadata(artist = "New artist"),
                openedAt = EpochMilliseconds(3_000L),
            ),
        )

        assertEquals(ID.value, reopened.mediaId)
        assertEquals(1_000L, reopened.addedAtEpochMs)
        assertEquals(3_000L, reopened.lastOpenedAtEpochMs)
        assertEquals("Original title", reopened.title)
        assertEquals("New artist", reopened.artist)
        assertEquals(CHECKPOINT.position.value, reopened.checkpointPositionMs)
    }

    @Test
    fun `older checkpoints cannot overwrite newer progress`() {
        val current = REGISTRATION.toNewEntity(ID).withCheckpoint(CHECKPOINT)
        val stale = current.withCheckpoint(
            CHECKPOINT.copy(
                position = Milliseconds(10L),
                updatedAt = EpochMilliseconds(CHECKPOINT.updatedAt.value - 1L),
            ),
        )

        assertEquals(current, stale)
    }

    @Test
    fun `entity without progress maps to a recent record without checkpoint`() {
        val recent = REGISTRATION.toNewEntity(ID).toRecentMedia()

        assertEquals(ID, recent.record.id)
        assertEquals(URI, recent.record.sourceUri)
        assertNull(recent.checkpoint)
    }

    private companion object {
        val ID = MediaId("stable-id")
        val URI = DurableMediaUri("content://provider/document/42")
        val REGISTRATION = MediaRegistration(
            sourceUri = URI,
            mimeType = "video/mp4",
            metadata = MediaMetadata(title = "Original title"),
            openedAt = EpochMilliseconds(1_000L),
        )
        val CHECKPOINT = PlaybackCheckpoint(
            mediaId = ID,
            position = Milliseconds(2_000L),
            duration = Milliseconds(10_000L),
            updatedAt = EpochMilliseconds(2_500L),
        )
    }
}
