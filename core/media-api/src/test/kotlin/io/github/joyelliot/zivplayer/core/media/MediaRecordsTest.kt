// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MediaRecordsTest {
    @Test(expected = IllegalArgumentException::class)
    fun `durable media URI rejects blank values`() {
        DurableMediaUri(" ")
    }

    @Test(expected = IllegalArgumentException::class)
    fun `durable media URI rejects process-local descriptors`() {
        DurableMediaUri("file:///proc/self/fd/42")
    }

    @Test(expected = IllegalArgumentException::class)
    fun `epoch milliseconds reject negative values`() {
        EpochMilliseconds(-1L)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `registration rejects blank MIME types`() {
        MediaRegistration(
            sourceUri = URI,
            mimeType = " ",
            openedAt = EpochMilliseconds(1L),
        )
    }

    @Test
    fun `checkpoint allows unknown duration without inventing metadata`() {
        val checkpoint = PlaybackCheckpoint(
            mediaId = MediaId("stable-id"),
            position = Milliseconds(12_345L),
            updatedAt = EpochMilliseconds(20_000L),
        )

        assertEquals(Milliseconds(12_345L), checkpoint.position)
        assertNull(checkpoint.duration)
    }

    private companion object {
        val URI = DurableMediaUri("content://media/document/42")
    }
}
