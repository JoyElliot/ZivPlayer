// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackAudioFocusPolicyTest {
    @Test fun deniedFocusNeverStartsOrResumesPlayback() {
        val policy = PlaybackAudioFocusPolicy()
        assertFalse(policy.requestPlay { false })
        assertEquals(PlaybackFocusAction.NONE, policy.onChange(PlaybackFocusChange.GAIN))
    }

    @Test fun temporaryLossAndDuckPauseThenResumeOnce() {
        for (change in listOf(PlaybackFocusChange.TRANSIENT_LOSS, PlaybackFocusChange.DUCK)) {
            val policy = PlaybackAudioFocusPolicy()
            assertTrue(policy.requestPlay { true })
            assertEquals(PlaybackFocusAction.PAUSE, policy.onChange(change))
            assertEquals(PlaybackFocusAction.RESUME, policy.onChange(PlaybackFocusChange.GAIN))
            assertEquals(PlaybackFocusAction.NONE, policy.onChange(PlaybackFocusChange.GAIN))
        }
    }

    @Test fun userPauseOrStopDuringInterruptionCancelsAutomaticResume() {
        val policy = PlaybackAudioFocusPolicy()
        policy.requestPlay { true }
        policy.onChange(PlaybackFocusChange.TRANSIENT_LOSS)
        policy.cancelPlaybackIntent()
        assertEquals(PlaybackFocusAction.NONE, policy.onChange(PlaybackFocusChange.GAIN))
        assertEquals(PlaybackFocusAction.NONE, policy.onChange(PlaybackFocusChange.TRANSIENT_LOSS))
    }

    @Test fun permanentLossAndUnplugRequireAnotherExplicitPlay() {
        for (change in listOf(PlaybackFocusChange.LOSS, PlaybackFocusChange.NOISY)) {
            val policy = PlaybackAudioFocusPolicy()
            policy.requestPlay { true }
            assertEquals(PlaybackFocusAction.PAUSE_AND_ABANDON, policy.onChange(change))
            assertEquals(PlaybackFocusAction.NONE, policy.onChange(PlaybackFocusChange.GAIN))
            assertFalse(policy.requestPlay { false })
            assertTrue(policy.requestPlay { true })
        }
    }

    @Test fun retainedFocusDoesNotIssueDuplicateRequests() {
        val policy = PlaybackAudioFocusPolicy()
        var requests = 0
        repeat(2) { assertTrue(policy.requestPlay { requests++; true }) }
        assertEquals(1, requests)
        policy.cancelPlaybackIntent()
        assertTrue(policy.requestPlay { requests++; true })
        assertEquals(2, requests)
    }
}
