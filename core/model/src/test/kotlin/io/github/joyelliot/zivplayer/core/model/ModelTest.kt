// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ModelTest {
    @Test
    fun identifiersRejectBlankValues() {
        assertThrows(IllegalArgumentException::class.java) { MediaId(" ") }
        assertThrows(IllegalArgumentException::class.java) { QueueItemId("") }
        assertThrows(IllegalArgumentException::class.java) { TrackId("\t") }
    }

    @Test
    fun playbackValuesEnforceTheirRanges() {
        assertThrows(IllegalArgumentException::class.java) { Milliseconds(-1) }
        assertThrows(IllegalArgumentException::class.java) { VolumePercent(101) }
        assertThrows(IllegalArgumentException::class.java) { PlaybackRatePermille(249) }
        assertThrows(IllegalArgumentException::class.java) { PlaybackRatePermille(4_001) }
    }

    @Test
    fun mediaSourceCopiesCallerOwnedHeaders() {
        val headers = mutableMapOf("Authorization" to "secret")
        val source = MediaSource("https://example.test/video.mkv", httpHeaders = headers)

        headers["Authorization"] = "changed"

        assertEquals("secret", source.httpHeaders["Authorization"])
    }
}
