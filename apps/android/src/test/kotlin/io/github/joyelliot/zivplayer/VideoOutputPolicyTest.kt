// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test

class VideoOutputPolicyTest {
    @Test fun directOutputRequiresEveryStableFullscreenCondition() {
        assertTrue(preferDirectVideoOutput(false, false, true, true, false, true, true))
        assertFalse(preferDirectVideoOutput(false, true, true, true, false, true, true))
        assertFalse(preferDirectVideoOutput(false, false, false, true, false, true, true))
        assertFalse(preferDirectVideoOutput(false, false, true, false, false, true, true))
        assertFalse(preferDirectVideoOutput(false, false, true, true, true, true, true))
        assertFalse(preferDirectVideoOutput(false, false, true, true, false, false, true))
        assertFalse(preferDirectVideoOutput(false, false, true, true, false, true, false))
    }

    @Test fun actualPipCanUseDirectOutputButEntryTransitionCannot() {
        assertTrue(preferDirectVideoOutput(true, false, false, false, false, false, false))
        assertFalse(preferDirectVideoOutput(true, true, true, true, false, true, true))
    }

    @Test fun closingAndInterruptedGesturesReturnToTextureBeforeTheEndpointMoves() {
        assertFalse(preferDirectVideoOutput(false, false, false, true, false, true, true))
        assertFalse(preferDirectVideoOutput(false, false, true, true, true, true, true))
    }
}
