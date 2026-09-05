// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VideoSurfaceRequestsTest {
    @Test
    fun oldResizeAndDisposalCannotAffectTheReplacementSurface() {
        val old = VideoSurfaceRequests.claim()
        val current = VideoSurfaceRequests.claim()
        assertFalse(VideoSurfaceRequests.isCurrent(old))
        VideoSurfaceRequests.release(old)
        assertTrue(VideoSurfaceRequests.isCurrent(current))
        VideoSurfaceRequests.release(current)
        assertFalse(VideoSurfaceRequests.isCurrent(current))
    }
}
