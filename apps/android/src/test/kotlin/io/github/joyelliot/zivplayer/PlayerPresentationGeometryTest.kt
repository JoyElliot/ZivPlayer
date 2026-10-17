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
}
