// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.data.media

import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnosticsJsonTest {
    @Test fun `export distinguishes unavailable values and safely quotes bounded strings`() {
        val json = PlaybackDiagnostics(videoCodec = "codec\"\n\\", droppedFrames = 0,
            cacheDurationSeconds = Double.NaN, engineVersion = "x".repeat(10_000)).toDiagnosticJson(12L, 34L)
        assertTrue(json.contains("\"droppedFrames\": 0"))
        assertTrue(json.contains("\"cacheUsedBytes\": null"))
        assertTrue(json.contains("\"cacheDurationSeconds\": null"))
        assertTrue(json.contains("codec\\\"\\n\\\\"))
        assertTrue(json.contains("\"sampledAtEpochMs\": 12"))
        assertFalse(json.contains("NaN"))
        assertTrue(json.length < 2_048)
    }
}
