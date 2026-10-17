// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test

class ConflatedSeekQueueTest {
    @Test fun stormHasOneInFlightAndReleaseWinsWithoutThrottleDelay() {
        val queue = ConflatedSeekQueue<String>()
        queue.offer(ScrubTarget("a", 100, false))
        assertEquals(100L, queue.take(0)?.positionMs)
        repeat(1_000) { queue.offer(ScrubTarget("a", it.toLong(), false)) }
        queue.offer(ScrubTarget("a", 999, true))
        assertNull(queue.take(1))
        queue.complete()
        assertEquals(0L, queue.delayUntilReady(1))
        assertEquals(ScrubTarget("a", 999, true), queue.take(1))
        queue.complete()
        assertNull(queue.delayUntilReady(100))
    }

    @Test fun previewsAreLimitedAndCancellationDiscardsPendingWork() {
        val queue = ConflatedSeekQueue<String>()
        queue.offer(ScrubTarget("a", 100, false))
        queue.take(10)
        queue.complete()
        queue.offer(ScrubTarget("a", 200, false))
        assertEquals(30L, queue.delayUntilReady(20))
        assertNull(queue.take(49))
        assertEquals(200L, queue.take(50)?.positionMs)
        queue.offer(ScrubTarget("b", 300, true))
        queue.clearPending()
        queue.complete()
        assertNull(queue.take(100))
    }
}
