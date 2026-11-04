// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.exoplayer

import android.graphics.SurfaceTexture
import android.view.Surface
import org.junit.Assert.*
import org.junit.Test

class OutputAndSubtitleTest {
    @Test fun returningToTheSameConsumerRejectsItsOldFirstFrame() {
        val texture = SurfaceTexture(false)
        val source = Surface(texture)
        try {
            val first = OwnedVideoOutput(source)
            val replacement = OwnedVideoOutput(source)
            try {
                assertTrue(first.accepts(first.surface))
                assertFalse(replacement.accepts(first.surface))
                assertTrue(replacement.accepts(replacement.surface))
                first.close()
                assertTrue(source.isValid)
                assertTrue(replacement.surface.isValid)
            } finally { first.close(); replacement.close() }
        } finally { source.release(); texture.release() }
    }

    @Test fun subtitlePreflightRejectsMalformedContentBeforeReplacingThePlayer() {
        validateSubtitleBytes("1\n00:00:00,000 --> 00:00:02,000\nHello\n\n".toByteArray(), "application/x-subrip")
        for (mime in listOf("text/vtt", "application/x-subrip", "text/x-ssa")) {
            assertThrows(Exception::class.java) { validateSubtitleBytes("not a subtitle".toByteArray(), mime) }
        }
    }
}
