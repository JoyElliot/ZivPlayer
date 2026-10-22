// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.assertEquals
import org.junit.Test

class PlayerControlInsetsGeometryTest {
    private val portrait = PlayerSafeArea(0, 111, 0, 0)
    private val landscape = PlayerSafeArea(111, 0, 0, 0)

    @Test fun insetsArrivingBeforeTheWindowCannotMoveTheControls() {
        val latch = PlayerControlInsetsLatch()
        assertEquals(portrait, latch.resolve({ portrait }, 0, 0f))
        assertEquals(landscape, latch.resolve({ portrait }, 0, 90f))
        assertEquals(landscape, latch.resolve({ landscape }, 0, 90f))
        assertEquals(landscape, latch.resolve({ landscape }, 1, 90f))
    }

    @Test fun windowArrivingBeforeInsetsCannotMoveTheControls() {
        val latch = PlayerControlInsetsLatch()
        latch.resolve({ portrait }, 0, 0f)
        latch.resolve({ portrait }, 0, 90f)
        assertEquals(landscape, latch.resolve({ portrait }, 1, 90f))
        assertEquals(landscape, latch.resolve({ landscape }, 1, 90f))
    }

    @Test fun returningToPortraitKeepsTheSafeAreaUntilBothCallbacksCatchUp() {
        val latch = PlayerControlInsetsLatch()
        latch.resolve({ portrait }, 0, 0f)
        latch.resolve({ landscape }, 1, 90f)
        assertEquals(portrait, latch.resolve({ landscape }, 1, 0f))
        assertEquals(portrait, latch.resolve({ landscape }, 0, 0f))
        assertEquals(portrait, latch.resolve({ portrait }, 0, 0f))
    }

    @Test fun initialInsetsCanSettleBeforeRotationStarts() {
        val latch = PlayerControlInsetsLatch()
        latch.resolve({ PlayerSafeArea(0, 0, 0, 0) }, 0, 0f)
        assertEquals(portrait, latch.resolve({ portrait }, 0, 0f))
        assertEquals(landscape, latch.resolve({ portrait }, 0, 90f))
    }

    @Test fun restoredRotatedWindowRecoversTheEntrySafeArea() {
        val latch = PlayerControlInsetsLatch()
        assertEquals(landscape, latch.resolve({ landscape }, 1, 90f))
        assertEquals(portrait, latch.resolve({ landscape }, 1, 0f))
    }

    @Test fun restoredWindowWaitsForItsFirstDispatchedInsets() {
        val latch = PlayerControlInsetsLatch()
        val empty = PlayerSafeArea(0, 0, 0, 0)
        assertEquals(empty, latch.resolve({ empty }, 1, 90f, sourceReady = false))
        assertEquals(landscape, latch.resolve({ landscape }, 1, 90f))
        assertEquals(portrait, latch.resolve({ landscape }, 1, 0f))
    }

    @Test fun previousWindowSnapshotCannotBecomeTheRestoredSafeArea() {
        val latch = PlayerControlInsetsLatch()
        latch.resolve({ portrait }, 1, 90f, sourceReady = false)
        assertEquals(landscape, latch.resolve({ landscape }, 1, 90f))
        assertEquals(portrait, latch.resolve({ landscape }, 1, 0f))
    }

    @Test fun aDispatchedEmptySafeAreaIsValidAndStaysStable() {
        val latch = PlayerControlInsetsLatch()
        val empty = PlayerSafeArea(0, 0, 0, 0)
        assertEquals(empty, latch.resolve({ empty }, 1, 90f))
        assertEquals(empty, latch.resolve({ landscape }, 1, 90f))
        assertEquals(empty, latch.resolve({ portrait }, 0, 0f))
    }

    @Test fun asymmetricInsetsKeepTheirSidesThroughAllQuarterTurns() {
        val entry = PlayerSafeArea(7, 111, 13, 24)
        for (turns in 0..3) {
            val latch = PlayerControlInsetsLatch()
            latch.resolve({ entry }, 0, 0f)
            val turned = entry.turned(turns)
            assertEquals(turned, latch.resolve({ turned }, turns, turns * 90f))
            assertEquals(entry, latch.resolve({ turned }, turns, 0f))
        }
    }
}
