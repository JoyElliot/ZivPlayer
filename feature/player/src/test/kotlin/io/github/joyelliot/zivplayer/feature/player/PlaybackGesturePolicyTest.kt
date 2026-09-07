// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackGesturePolicyTest {
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
