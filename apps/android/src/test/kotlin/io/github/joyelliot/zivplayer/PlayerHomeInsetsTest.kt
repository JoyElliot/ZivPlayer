// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.assertEquals
import org.junit.Test

class PlayerHomeInsetsTest {
    private val portrait = PlayerSafeArea(0, 111, 0, 24)
    private val landscape = portrait.turned(1)

    @Test fun homeAndDockKeepTheirEntryInsetsAcrossBothCallbackOrders() {
        for (insetsFirst in listOf(false, true)) {
            val latch = PlayerHomeInsetLatch()
            assertEquals(portrait, latch.resolve(portrait, true, false, 0))
            assertEquals(portrait, latch.resolve(portrait, true, true, 0))
            assertEquals(portrait, latch.resolve(if (insetsFirst) landscape else portrait, true, true, if (insetsFirst) 0 else 1))
            assertEquals(portrait, latch.resolve(landscape, true, true, 1))
            assertEquals(portrait, latch.resolve(portrait, true, true, 1))
            assertEquals(portrait, latch.resolve(portrait, true, true, 0))
            assertEquals(portrait, latch.resolve(portrait, true, false, 0))
        }
    }

    @Test fun restoredCanvasDoesNotFreezeAnUndispatchedSnapshot() {
        val latch = PlayerHomeInsetLatch()
        latch.resolve(PlayerSafeArea(0, 0, 0, 0), false, true, 1)
        assertEquals(portrait, latch.resolve(landscape, true, true, 1))
    }

    @Test fun leavingThePresentationAllowsRealInsetChangesAgain() {
        val latch = PlayerHomeInsetLatch()
        latch.resolve(portrait, true, true, 0)
        val resized = PlayerSafeArea(10, 20, 30, 40)
        assertEquals(portrait, latch.resolve(resized, false, false, 0))
        assertEquals(resized, latch.resolve(resized, true, false, 0))
    }
}
