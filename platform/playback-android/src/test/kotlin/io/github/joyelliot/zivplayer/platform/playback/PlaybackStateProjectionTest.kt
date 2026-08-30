// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import androidx.media3.common.Player
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaItem
import io.github.joyelliot.zivplayer.core.model.MediaSource
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.PlaybackQueue
import io.github.joyelliot.zivplayer.core.player.PlaybackSnapshot
import io.github.joyelliot.zivplayer.core.player.PlayerCapabilities
import io.github.joyelliot.zivplayer.core.player.PlayerCapability
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.PlayerErrorKind
import io.github.joyelliot.zivplayer.core.player.PlayerStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackStateProjectionTest {
    @Test
    fun `queue occurrence IDs are deterministic and distinct from stable media IDs`() {
        val generator = QueueItemIdGenerator(instanceId = "test")

        assertEquals(QueueItemId("queue:test:0"), generator.next())
        assertEquals(QueueItemId("queue:test:1"), generator.next())
        assertFalse(generator.next().value == ITEM.media.id.value)
    }

    @Test
    fun `one stable media ID may have multiple queue occurrences`() {
        val generator = QueueItemIdGenerator(instanceId = "repeat")
        val repeated = listOf(
            ITEM.copy(id = generator.next()),
            ITEM.copy(id = generator.next()),
        )

        val queue = PlaybackQueue.of(repeated, currentIndex = 0)

        assertEquals(2, queue.items.map(QueueItem::id).distinct().size)
        assertEquals(1, queue.items.map { it.media.id }.distinct().size)
    }

    @Test
    fun `every core status maps to a valid Media3 state tuple`() {
        val expectations = listOf(
            Expectation(PlayerStatus.IDLE, Player.STATE_IDLE),
            Expectation(PlayerStatus.LOADING, Player.STATE_BUFFERING, isLoading = true),
            Expectation(PlayerStatus.READY, Player.STATE_READY),
            Expectation(PlayerStatus.PLAYING, Player.STATE_READY, playWhenReady = true),
            Expectation(PlayerStatus.PAUSED, Player.STATE_READY),
            Expectation(PlayerStatus.BUFFERING, Player.STATE_BUFFERING, isLoading = true),
            Expectation(PlayerStatus.ENDED, Player.STATE_ENDED),
            Expectation(PlayerStatus.ERROR, Player.STATE_IDLE, exposesError = true),
            Expectation(PlayerStatus.CLOSED, Player.STATE_IDLE),
        )

        expectations.forEach { expected ->
            val snapshot = snapshot(expected)
            val projection = snapshot.toMedia3Projection()
            assertEquals(expected.status.name, expected.media3State, projection.playbackState)
            assertEquals(expected.status.name, expected.isLoading, projection.isLoading)
            assertEquals(expected.status.name, expected.exposesError, projection.exposesError)
        }
    }

    @Test
    fun `reset errors retain replacement command but suppress stale-engine commands`() {
        val commands = PlaybackSnapshot(
            status = PlayerStatus.ERROR,
            error = PlayerError(
                kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
                message = "native backend must be rebuilt",
                recovery = ErrorRecovery.RESET,
            ),
        ).media3CommandPolicy()

        assertTrue(commands.contains(Player.COMMAND_SET_MEDIA_ITEM))
        assertTrue(commands.contains(Player.COMMAND_RELEASE))
        assertTrue(commands.contains(Player.COMMAND_SET_VIDEO_SURFACE))
        assertFalse(commands.contains(Player.COMMAND_PREPARE))
        assertFalse(commands.contains(Player.COMMAND_PLAY_PAUSE))
    }

    @Test
    fun `play-pause command follows the action required by current intent`() {
        val playable = activeSnapshot(
            status = PlayerStatus.READY,
            playWhenReady = false,
            available = setOf(PlayerCapability.PLAY),
        )
        val pausable = activeSnapshot(
            status = PlayerStatus.PLAYING,
            playWhenReady = true,
            available = setOf(PlayerCapability.PAUSE),
        )

        assertTrue(playable.media3CommandPolicy().contains(Player.COMMAND_PLAY_PAUSE))
        assertTrue(pausable.media3CommandPolicy().contains(Player.COMMAND_PLAY_PAUSE))
        assertFalse(
            playable.copy(
                capabilities = capabilities(PlayerCapability.PAUSE),
            ).media3CommandPolicy().contains(Player.COMMAND_PLAY_PAUSE),
        )
    }

    @Test
    fun `volume command requires both volume and mute capabilities`() {
        val volumeOnly = PlaybackSnapshot(
            capabilities = capabilities(PlayerCapability.SET_VOLUME),
        )
        val complete = PlaybackSnapshot(
            capabilities = capabilities(
                PlayerCapability.SET_VOLUME,
                PlayerCapability.SET_MUTED,
            ),
        )

        assertFalse(volumeOnly.media3CommandPolicy().contains(Player.COMMAND_SET_VOLUME))
        assertTrue(complete.media3CommandPolicy().contains(Player.COMMAND_SET_VOLUME))
    }

    private fun snapshot(expectation: Expectation): PlaybackSnapshot {
        val active = expectation.status !in setOf(
            PlayerStatus.IDLE,
            PlayerStatus.ERROR,
            PlayerStatus.CLOSED,
        )
        return PlaybackSnapshot(
            status = expectation.status,
            playWhenReady = expectation.playWhenReady,
            queue = if (active) PlaybackQueue.of(listOf(ITEM), 0) else PlaybackQueue.EMPTY,
            error = if (expectation.status == PlayerStatus.ERROR) {
                PlayerError(PlayerErrorKind.BACKEND_OPERATION_FAILED, "test failure")
            } else {
                null
            },
        )
    }

    private fun activeSnapshot(
        status: PlayerStatus,
        playWhenReady: Boolean,
        available: Set<PlayerCapability>,
    ): PlaybackSnapshot = PlaybackSnapshot(
        status = status,
        playWhenReady = playWhenReady,
        queue = PlaybackQueue.of(listOf(ITEM), 0),
        capabilities = PlayerCapabilities.of(
            supported = available,
            available = available,
        ),
    )

    private fun capabilities(
        vararg available: PlayerCapability,
    ): PlayerCapabilities = PlayerCapabilities.of(
        supported = available.toSet(),
        available = available.toSet(),
    )

    private data class Expectation(
        val status: PlayerStatus,
        val media3State: Int,
        val isLoading: Boolean = false,
        val exposesError: Boolean = false,
        val playWhenReady: Boolean = false,
    )

    private companion object {
        val ITEM = QueueItem(
            id = QueueItemId("queue-item"),
            media = MediaItem(
                id = MediaId("media-item"),
                source = MediaSource("content://test/media"),
            ),
        )
    }
}
