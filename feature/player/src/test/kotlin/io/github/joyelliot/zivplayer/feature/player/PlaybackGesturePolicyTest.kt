// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackGesturePolicyTest {
    @Test fun doubleTapSeeksFiveSecondsInOuterThirdsAndPreservesCenterToggle() {
        assertEquals(-5_000L, doubleTapSeekDelta(0.1f))
        assertEquals(-5_000L, doubleTapSeekDelta(0.32f))
        assertEquals(0L, doubleTapSeekDelta(1f / 3f))
        assertEquals(0L, doubleTapSeekDelta(0.5f))
        assertEquals(0L, doubleTapSeekDelta(2f / 3f))
        assertEquals(5_000L, doubleTapSeekDelta(0.68f))
        assertEquals(5_000L, doubleTapSeekDelta(0.9f))
    }

    @Test fun doubleTapClampsToActualMediaEndpoints() {
        assertEquals(0L, doubleTapSeekPosition(2_000L, 60_000L, -5_000L))
        assertEquals(60_000L, doubleTapSeekPosition(58_000L, 60_000L, 5_000L))
        assertEquals(35_000L, doubleTapSeekPosition(30_000L, 60_000L, 5_000L))
        assertEquals(0L, doubleTapSeekPosition(30_000L, 0L, 5_000L))
    }

    @Test fun verticalGesturesRaiseLevelsUpwardAndClampAtBothEnds() {
        assertEquals(0.8f, gestureLevel(0.5f, -0.2f), 0.001f)
        assertEquals(0.2f, gestureLevel(0.5f, 0.2f), 0.001f)
        assertEquals(1f, gestureLevel(0.5f, -1f), 0f)
        assertEquals(0f, gestureLevel(0.5f, 1f), 0f)
        assertEquals(0.5f, gestureLevel(0.5f, Float.NaN), 0f)
    }

    @Test fun horizontalSeekUsesOriginalPositionAndClampsBothEnds() {
        assertEquals(75_000L, gestureSeekPosition(45_000L, 7_200_000L, 0.25f))
        assertEquals(15_000L, gestureSeekPosition(45_000L, 7_200_000L, -0.25f))
        assertEquals(0L, gestureSeekPosition(1_000L, 10_000L, -0.5f))
        assertEquals(10_000L, gestureSeekPosition(9_000L, 10_000L, 0.5f))
    }

    @Test fun shortMediaUsesItsDurationAndUnknownDurationDoesNotSeek() {
        assertEquals(7_500L, gestureSeekPosition(5_000L, 10_000L, 0.25f))
        assertEquals(5_000L, gestureSeekPosition(5_000L, 0L, 0.5f))
        assertEquals(5_000L, gestureSeekPosition(5_000L, 10_000L, Float.NaN))
    }

    @Test fun heldGestureCanReachSlowMotionAndFastForward() {
        assertEquals(2f, gesturePlaybackSpeed(0f))
        assertEquals(1f, gesturePlaybackSpeed(-0.25f))
        assertEquals(0.25f, gesturePlaybackSpeed(-1f))
        assertEquals(4f, gesturePlaybackSpeed(1f))
        assertEquals(2.25f, gesturePlaybackSpeed(0.06f))
        assertEquals(2f, gesturePlaybackSpeed(Float.NaN))
    }
}
