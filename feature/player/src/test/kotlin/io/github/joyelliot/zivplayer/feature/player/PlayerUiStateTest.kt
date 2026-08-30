// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import org.junit.Assert.assertEquals
import org.junit.Test

class PlayerUiStateTest {
    @Test
    fun `time formatting uses compact minute and hour labels`() {
        val cases = mapOf(
            0L to "0:00",
            999L to "0:00",
            1_000L to "0:01",
            59_999L to "0:59",
            60_000L to "1:00",
            3_661_000L to "1:01:01",
            -1L to "0:00",
        )

        cases.forEach { (milliseconds, expected) ->
            assertEquals(expected, formatPlaybackTime(milliseconds))
        }
    }

    @Test
    fun `speed formatting is bounded and locale independent`() {
        assertEquals("0.25×", formatPlaybackSpeed(0f))
        assertEquals("0.75×", formatPlaybackSpeed(0.75f))
        assertEquals("1×", formatPlaybackSpeed(1f))
        assertEquals("1.25×", formatPlaybackSpeed(1.25f))
        assertEquals("4×", formatPlaybackSpeed(5f))
    }

    @Test
    fun `timeline progress clamps position to known duration`() {
        assertEquals(
            0.5f,
            PlayerUiState(positionMs = 50L, durationMs = 100L).timelineProgress,
        )
        assertEquals(
            1f,
            PlayerUiState(positionMs = 150L, durationMs = 100L).timelineProgress,
        )
        assertEquals(0f, PlayerUiState(positionMs = 50L).timelineProgress)
    }

    @Test
    fun `speed steps advance and wrap`() {
        assertEquals(0.75f, nextPlaybackSpeed(0.5f))
        assertEquals(1.25f, nextPlaybackSpeed(1f))
        assertEquals(0.5f, nextPlaybackSpeed(2f))
    }

    @Test
    fun `recent progress requires a positive duration`() {
        assertEquals(
            0.25f,
            RecentMediaUiItem(
                mediaId = "media",
                title = "Example",
                positionMs = 25L,
                durationMs = 100L,
            ).progress,
        )
        assertEquals(
            null,
            RecentMediaUiItem(mediaId = "media", title = "Example").progress,
        )
    }
}
