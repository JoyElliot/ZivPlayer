// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player

import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaItem
import io.github.joyelliot.zivplayer.core.model.MediaSource
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackStateTest {
    @Test
    fun queueRequiresUniqueIdsAndAValidCurrentIndex() {
        val item = queueItem("one")

        assertThrows(IllegalArgumentException::class.java) {
            PlaybackQueue.of(listOf(item, item), currentIndex = 0)
        }
        assertThrows(IllegalArgumentException::class.java) {
            PlaybackQueue.of(listOf(item), currentIndex = 1)
        }
    }

    @Test
    fun normalizedTimelineBoundsBackendRoundingAtDuration() {
        val timeline = PlaybackTimeline.normalized(
            position = Milliseconds(1_001),
            duration = Milliseconds(1_000),
            bufferedPosition = Milliseconds(1_500),
        )

        assertEquals(Milliseconds(1_000), timeline.position)
        assertEquals(Milliseconds(1_000), timeline.bufferedPosition)
    }

    @Test
    fun normalizedTimelineNeverReportsBufferingBehindPosition() {
        val timeline = PlaybackTimeline.normalized(
            position = Milliseconds(800),
            duration = Milliseconds(1_000),
            bufferedPosition = Milliseconds(500),
        )

        assertEquals(Milliseconds(800), timeline.bufferedPosition)
    }

    @Test
    fun setQueueCopiesCallerOwnedItemsImmediately() {
        val items = mutableListOf(queueItem("one"))
        val command = PlayerCommand.SetQueue(items)

        items.clear()

        assertEquals(1, command.items.size)
        assertTrue(command.items !== items)
    }

    @Test
    fun errorAndStatusMustRemainConsistent() {
        val queue = PlaybackQueue.of(listOf(queueItem("one")), currentIndex = 0)

        assertThrows(IllegalArgumentException::class.java) {
            PlaybackSnapshot(
                status = PlayerStatus.ERROR,
                queue = queue,
                error = null,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            PlaybackSnapshot(
                status = PlayerStatus.READY,
                queue = queue,
                error = PlayerError(PlayerErrorKind.SOURCE_UNAVAILABLE, "Unavailable"),
            )
        }
    }

    @Test
    fun trackSnapshotRejectsDuplicateAndMismatchedSelection() {
        val audio = TrackDescriptor(TrackId("audio"), TrackKind.AUDIO)

        assertThrows(IllegalArgumentException::class.java) {
            TrackSnapshot.of(listOf(audio, audio), emptyMap())
        }
        assertThrows(IllegalArgumentException::class.java) {
            TrackSnapshot.of(
                available = listOf(audio),
                selected = mapOf(TrackKind.SUBTITLE to TrackId("audio")),
            )
        }
    }

    private fun queueItem(id: String): QueueItem =
        QueueItem(
            id = QueueItemId(id),
            media = MediaItem(
                id = MediaId("media-$id"),
                source = MediaSource("https://example.test/$id.mkv"),
            ),
        )
}
