// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.pow

class MediaVolumePolicyTest {
    @Test fun coarseCurvesInterpolateContinuouslyWithoutBoosting() {
        fun db(index: Int) = if (index == 0) Float.NEGATIVE_INFINITY else -60f + index * 4f
        var previous = 0f
        for (percent in 0..1_000) {
            val step = mediaVolumeStep(percent / 1_000f, 0, 15, ::db)
            assertTrue(step.index in 0..15 && step.gain in 0f..1f)
            val amplitude = 10.0.pow(db(step.index) / 20.0).toFloat() * step.gain
            assertTrue(amplitude + 0.00001f >= previous)
            previous = amplitude
        }
        assertEquals(MediaVolumeStep(0, 0f), mediaVolumeStep(0f, 0, 15))
        assertEquals(MediaVolumeStep(15, 1f), mediaVolumeStep(1f, 0, 15))
    }
    @Test fun unknownRoutesAndNonzeroMinimumStayBounded() {
        var previous = 0f
        for (percent in 0..1_000) {
            val step = mediaVolumeStep(percent / 1_000f, 2, 17)
            assertTrue(step.index in 2..17 && step.gain in 0f..1f)
            val amplitude = 10.0.pow((-60f + step.index * 4f) / 20.0).toFloat() * step.gain
            assertTrue(amplitude + 0.00001f >= previous)
            previous = amplitude
        }
        assertEquals(1f, mediaVolumeStep(0.504f, 0, 100).gain)
        assertEquals(MediaVolumeStep(7, 1f), mediaVolumeStep(0.2f, 7, 7))
        assertEquals(MediaVolumeStep(0, 0f), mediaVolumeStep(Float.NaN, 0, 15))
    }
}
