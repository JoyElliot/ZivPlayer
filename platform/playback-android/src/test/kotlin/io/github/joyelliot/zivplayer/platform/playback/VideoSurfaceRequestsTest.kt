// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VideoSurfaceRequestsTest {
    @Test fun oldFirstFrameCannotRevealTheReplacementSurface() {
        val old = VideoSurfaceRequests.claim()
        VideoSurfaceRequests.markRendered(old)
        assertEquals(old, VideoSurfaceRequests.renderedToken.value)
        val current = VideoSurfaceRequests.claim()
        VideoSurfaceRequests.markRendered(old)
        assertEquals(0L, VideoSurfaceRequests.renderedToken.value)
        VideoSurfaceRequests.markRendered(current)
        assertEquals(current, VideoSurfaceRequests.renderedToken.value)
        VideoSurfaceRequests.release(current)
    }

    @Test
    fun oldResizeAndDisposalCannotAffectTheReplacementSurface() {
        val old = VideoSurfaceRequests.claim()
        val current = VideoSurfaceRequests.claim()
        assertFalse(VideoSurfaceRequests.isCurrent(old))
        assertFalse(VideoSurfaceRequests.release(old))
        assertTrue(VideoSurfaceRequests.isCurrent(current))
        assertTrue(VideoSurfaceRequests.release(current))
        assertFalse(VideoSurfaceRequests.isCurrent(current))
        assertFalse(VideoSurfaceRequests.release(current))
        assertFalse(VideoSurfaceRequests.release(0))
    }
}
