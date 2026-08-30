// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SetMediaRequestFenceTest {
    @Test
    fun `new media request supersedes an unresolved request`() {
        var latestSequence = 1L
        val fence = fence(
            currentSequence = { latestSequence },
            invalidateSequence = { ++latestSequence },
        )
        val first = fence.begin(latestSequence)
        latestSequence = 2L
        val second = fence.begin(latestSequence)

        assertFalse(fence.isCurrent(first))
        assertTrue(fence.isCurrent(second))
    }

    @Test
    fun `late arrival with an older source sequence cannot supersede the latest request`() {
        var latestSequence = 2L
        val fence = fence(currentSequence = { latestSequence })
        val latest = fence.begin(2L)
        val lateOlder = fence.begin(1L)

        assertTrue(fence.isCurrent(latest))
        assertFalse(fence.isCurrent(lateOlder))
    }

    @Test
    fun `explicit transport action invalidates an unresolved media request`() {
        var latestSequence = 1L
        val fence = fence(
            currentSequence = { latestSequence },
            invalidateSequence = { ++latestSequence },
        )
        val request = fence.begin(latestSequence)

        fence.invalidate()

        assertFalse(fence.isCurrent(request))
    }

    @Test
    fun `play is held while the latest media request is unresolved`() {
        var latestSequence = 1L
        val fence = fence(currentSequence = { latestSequence })
        val request = fence.begin(latestSequence)

        assertTrue(fence.hasPendingRequest())

        fence.finish(request)

        assertFalse(fence.hasPendingRequest())
    }

    @Test
    fun `client cancellation releases play even before the resolver finishes`() {
        var latestSequence = 1L
        val fence = fence(currentSequence = { latestSequence })
        val cancelled = fence.begin(latestSequence)

        latestSequence = 2L

        assertFalse(fence.isCurrent(cancelled))
        assertFalse(fence.hasPendingRequest())
    }

    private fun fence(
        currentSequence: () -> Long,
        invalidateSequence: () -> Unit = {},
    ): SetMediaRequestFence = SetMediaRequestFence(
        nextSequence = { error("No unsequenced request expected in this test.") },
        currentSequence = currentSequence,
        invalidateSequence = invalidateSequence,
    )
}
