// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test

class PlayerRotationGeometryTest {
    @Test fun systemRotationPreservesLogicalSizeAndPhysicalVideoAngle() {
        for (base in 0..3) for (turn in 0..3) {
            val current = (base + turn) % 4
            val basis = PlayerRotationBasis(base, false)
            val latch = PlayerWindowRotationLatch(base)
            val swap = turn % 2 != 0
            val canvas = latch.measure(basis, current, if (swap) 800 else 400, if (swap) 400 else 800)
            assertEquals(400, canvas.width)
            assertEquals(800, canvas.height)
            val physicalAngle = turn * 90f + canvas.compensation + 90f
            assertEquals(90f, (physicalAngle + 360) % 360, 0.001f)
        }
    }

    @Test fun earlyConfigurationDoesNotCancelTheVideoRotationInOldWindowDimensions() {
        val basis = PlayerRotationBasis(0, false)
        val latch = PlayerWindowRotationLatch(0)
        assertEquals(0f, latch.measure(basis, 1, 400, 800).compensation, 0f)
        assertEquals(-90f, latch.measure(basis, 1, 800, 400).compensation, 0f)
        assertEquals(-90f, latch.measure(basis, 0, 800, 400).compensation, 0f)
        assertEquals(0f, latch.measure(basis, 0, 400, 800).compensation, 0f)
    }

    @Test fun transformedMiniAnchorReturnsToTheSameLogicalSlot() {
        val expected = VideoFrame(60f, 740f, 56f, 32f)
        val windows = listOf(expected, VideoFrame(740f, 340f, 32f, 56f),
            VideoFrame(340f, 60f, 56f, 32f), VideoFrame(60f, 60f, 32f, 56f))
        windows.forEachIndexed { turns, physical ->
            val canvas = PlayerCanvasGeometry(if (turns % 2 == 0) 400 else 800,
                if (turns % 2 == 0) 800 else 400, turns)
            assertEquals(expected, canvas.windowToCanvas(physical))
        }
    }

    @Test fun nativeLandscapeAndReverseDefaultPoliciesMapEveryQuarterTurn() {
        assertEquals(listOf(1, 0, 9, 8), (0..3).map { fixedOrientationForRotation(it, false, false) })
        assertEquals(listOf(1, 8, 9, 0), (0..3).map { fixedOrientationForRotation(it, false, true) })
        assertEquals(listOf(0, 9, 8, 1), (0..3).map { fixedOrientationForRotation(it, true, false) })
        assertEquals(listOf(0, 1, 8, 9), (0..3).map { fixedOrientationForRotation(it, true, true) })
    }

    @Test fun landscapeEntryAndReversePortraitUseTheirOwnBaseline() {
        val basis = PlayerRotationBasis(3, true)
        assertEquals(0, basis.targetRotation(true))
        assertFalse(basis.isLandscapeAt(0))
        assertTrue(basis.isLandscapeAt(3))
        val canvas = PlayerWindowRotationLatch(3).measure(basis, 0, 400, 800)
        assertEquals(800, canvas.width)
        assertEquals(400, canvas.height)
    }

    @Test fun miniatureIsUprightAndFitsThePhysicalSlotWhileTheWindowReturns() {
        for (turns in 0..3) for (aspect in listOf(16f / 9f, 9f / 16f, 1f)) {
            val canvas = PlayerCanvasGeometry(800, 400, turns)
            val mini = VideoFrame(120f, 340f, 56f, 32f)
            val frame = presentationFrame(canvas.width.toFloat(), canvas.height.toFloat(), aspect,
                canvas.windowToCanvas(mini), 0f, 0f, miniRotation = -canvas.compensation)
            assertEquals(0f, frame.rotation + canvas.compensation, 0f)
            assertTrue(frame.width <= mini.width && frame.height <= mini.height)
            assertEquals(aspect, frame.width / frame.height, 0.0001f)
        }
    }

    @Test fun fullVideoKeepsItsLogicalGeometryWhenTheSystemTakesOver() {
        for (aspect in listOf(16f / 9f, 9f / 16f, 1f)) for (fit in 0..2) {
            val anchor = VideoFrame(60f, 740f, 56f, 32f)
            val before = presentationFrame(400f, 800f, aspect, anchor, 1f, 90f, fill = fit == 1, stretch = fit == 2)
            val canvas = PlayerCanvasGeometry(800, 400, 1)
            val after = presentationFrame(canvas.width.toFloat(), canvas.height.toFloat(), aspect,
                canvas.windowToCanvas(VideoFrame(740f, 340f, 32f, 56f)), 1f, 90f,
                fill = fit == 1, stretch = fit == 2, miniRotation = -canvas.compensation)
            assertEquals(before, after)
        }
    }

    @Test fun halfTurnDoesNotSwapTheControlsOrVideoAxes() {
        assertEquals(0f, rotationAxisSwap(180f), 0f)
        assertEquals(0f, rotationAxisSwap(-180f), 0f)
        assertEquals(1f, rotationAxisSwap(-90f), 0f)
        assertEquals(0.5f, rotationAxisSwap(135f), 0f)
    }

    @Test fun earlyDimensionsDoNotReflowTheLogicalCanvasBeforeRotationArrives() {
        val basis = PlayerRotationBasis(0, false)
        val latch = PlayerWindowRotationLatch(0)
        latch.measure(basis, 0, 400, 800)
        val early = latch.measure(basis, 0, 800, 400)
        assertEquals(400, early.width)
        assertEquals(800, early.height)
        assertEquals(0, early.turns)
        val ready = latch.measure(basis, 1, 800, 400)
        assertEquals(400, ready.width)
        assertEquals(800, ready.height)
        assertEquals(1, ready.turns)
    }

    @Test fun freeformResizeDoesNotWaitForADisplayRotationThatWillNeverArrive() {
        val basis = PlayerRotationBasis(0, false)
        val canvas = PlayerWindowRotationLatch(0).measure(basis, 0, 800, 400, multiWindow = true)
        assertEquals(800, canvas.width)
        assertEquals(400, canvas.height)
    }

    @Test fun autoPipOnlyAcceptsAnUprightWindowAlignedFrame() {
        assertTrue(isVideoAlignedWithWindow(0f, 0f))
        assertTrue(isVideoAlignedWithWindow(90f, -90f))
        assertFalse(isVideoAlignedWithWindow(45f, 0f))
        assertFalse(isVideoAlignedWithWindow(90f, 0f))
        assertFalse(isVideoAlignedWithWindow(90f, 90f))
    }
}
