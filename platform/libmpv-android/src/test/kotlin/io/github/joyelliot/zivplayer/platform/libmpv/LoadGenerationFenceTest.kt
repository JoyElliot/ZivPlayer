// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LoadGenerationFenceTest {
    @Test
    fun replacementDoesNotArmNewGenerationBeforeOldEndAndNewStart() {
        val fence = LoadGenerationFence()
        val first = LoadGeneration(1)
        val second = LoadGeneration(2)

        val firstStarted = fence.beginLoad(first)
        assertNull(fence.activeGeneration())
        assertEquals(first, fence.onStartFile())
        assertTrue(firstStarted.isCompleted)
        assertEquals(first, fence.activeGeneration())

        val firstStopped = fence.beginStop()!!
        assertFalse(firstStopped.isCompleted)
        assertEquals(first, fence.activeGeneration())
        assertEquals(first, fence.onEndFile())
        assertTrue(firstStopped.isCompleted)
        assertNull(fence.activeGeneration())

        val secondStarted = fence.beginLoad(second)
        assertNull(fence.activeGeneration())
        assertEquals(second, fence.onStartFile())
        assertTrue(secondStarted.isCompleted)
        assertEquals(second, fence.activeGeneration())
    }

    @Test
    fun cancelledLoadFailsClosedAndCannotBeArmedByALateStart() {
        val fence = LoadGenerationFence()
        val generation = LoadGeneration(1)

        val started = fence.beginLoad(generation)
        fence.cancelLoad(generation)

        assertTrue(started.isCancelled)
        assertNull(fence.onStartFile())
        assertNull(fence.activeGeneration())
    }

    @Test
    fun naturalEndClearsGenerationWithoutAStopWaiter() {
        val fence = LoadGenerationFence()
        val generation = LoadGeneration(7)

        fence.beginLoad(generation)
        fence.onStartFile()

        assertEquals(generation, fence.onEndFile())
        assertNull(fence.activeGeneration())
        assertNull(fence.beginStop())
    }

    @Test
    fun pendingLoadCanBeStoppedBeforeStartWithoutArmingLateCallback() {
        val fence = LoadGenerationFence()
        val generation = LoadGeneration(3)

        val started = fence.beginLoad(generation)
        val stopped = fence.beginStop()!!
        assertFalse(stopped.isCompleted)

        assertNull(fence.onEndFile())
        assertTrue(started.isCancelled)
        assertTrue(stopped.isCompleted)
        assertNull(fence.activeGeneration())
        assertNull(fence.onStartFile())
    }

    @Test
    fun terminalFailureClearsAPendingLoadBeforeStart() {
        val fence = LoadGenerationFence()
        val generation = LoadGeneration(4)

        val started = fence.beginLoad(generation)

        assertEquals(generation, fence.failCurrent())
        assertTrue(started.isCancelled)
        assertNull(fence.activeGeneration())
        assertNull(fence.onStartFile())
        assertNull(fence.failCurrent())
    }

    @Test
    fun terminalFailureReleasesAnActiveStopWaiter() {
        val fence = LoadGenerationFence()
        val generation = LoadGeneration(5)
        fence.beginLoad(generation)
        fence.onStartFile()
        val stopped = fence.beginStop()!!

        assertEquals(generation, fence.failCurrent())
        assertTrue(stopped.isCompleted)
        assertNull(fence.activeGeneration())
    }
}
