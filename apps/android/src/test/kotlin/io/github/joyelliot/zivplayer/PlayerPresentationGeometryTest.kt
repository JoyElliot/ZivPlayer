// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test

class PlayerPresentationGeometryTest {
    private val mini = VideoFrame(60f, 740f, 56f, 36f)
    @Test fun endpointsPreserveAspectAndRotateIntoLandscapeBounds() {
        val collapsed = presentationFrame(400f, 800f, 16f / 9, mini, 0f, 90f)
        assertEquals(60f, collapsed.centerX)
        assertEquals(56f, collapsed.width)
        assertEquals(0f, collapsed.rotation)
        val full = presentationFrame(400f, 800f, 16f / 9, mini, 1f, 90f)
        assertEquals(400f, full.boundingWidth, 0.001f)
        assertTrue(full.boundingHeight <= 800f)
        assertEquals(200f, full.centerX)
        assertEquals(400f, full.centerY)
    }
    @Test fun dragAndAnimationShareTheSameContinuousGeometry() {
        val before = presentationFrame(400f, 800f, 9f / 16, mini, 0.499f, 0f)
        val after = presentationFrame(400f, 800f, 9f / 16, mini, 0.501f, 0f)
        assertTrue(kotlin.math.abs(after.width - before.width) < 2f)
        assertTrue(kotlin.math.abs(after.centerY - before.centerY) < 2f)
        assertEquals(9f / 16, after.width / after.height, 0.0001f)
    }
    @Test fun velocityCanReverseAnIncompleteExpansion() {
        assertFalse(settleExpanded(0.8f, -900f))
        assertTrue(settleExpanded(0.1f, 900f))
        assertFalse(settleExpanded(0.2f, 0f))
        assertTrue(settleExpanded(0.7f, 0f))
    }

    @Test fun fullscreenRotationNeverPumpsPastEitherFittedEndpoint() {
        for (aspect in listOf(16f / 9, 9f / 16, 2.4f, 1f)) for (fill in listOf(false, true)) {
            fun at(angle: Float) = presentationFrame(1080f, 2400f, aspect, mini, 1f, angle, fill = fill)
            val portrait = at(0f)
            val landscape = at(90f)
            var previous = landscape.width
            for (angle in 89 downTo 0) {
                val frame = at(angle.toFloat())
                val low = minOf(portrait.width, landscape.width) - 0.001f
                val high = maxOf(portrait.width, landscape.width) + 0.001f
                assertTrue("angle=$angle aspect=$aspect fill=$fill", frame.width in low..high)
                assertTrue((frame.width - previous) * (portrait.width - landscape.width) >= -0.01f)
                assertEquals(aspect, frame.width / frame.height, 0.0001f)
                assertEquals(frame.width, at(-angle.toFloat()).width, 0.001f)
                previous = frame.width
            }
        }
    }
}
