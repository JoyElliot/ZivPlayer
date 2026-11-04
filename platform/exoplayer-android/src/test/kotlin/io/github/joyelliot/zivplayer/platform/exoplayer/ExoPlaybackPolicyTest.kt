// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.exoplayer

import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import org.junit.Assert.*
import org.junit.Test

class ExoPlaybackPolicyTest {
    @Test fun opaqueDocumentUrisUseTheDisplayName() {
        assertEquals("text/x-ssa", subtitleMimeType("content://documents/document/1234", "episode.ASS"))
        assertEquals("application/x-subrip", subtitleMimeType("file:///cache/a.srt?query=1", null))
        assertEquals("text/vtt", subtitleMimeType("content://documents/document/1234", "sub.vtt"))
    }
    @Test(expected = IllegalStateException::class)
    fun unknownSubtitleFormatsAreRejectedInsteadOfGuessed() { subtitleMimeType("content://documents/1", "bitmap.sub") }

    @Test fun timingOptionsAreAppliedAndDefaultsAreAccepted() {
        assertNull(unsupportedSettings(PlaybackOptions()))
        assertNull(unsupportedSettings(PlaybackOptions(audioDelayMs = 500, subtitleDelayMs = -1000)))
    }

    @Test fun smallAndLargeAudioAdvancesSatisfyMedia3BufferConstraints() {
        for (delay in listOf(-10_000, -1000, -999, -50, -1, 0, 50, 10_000)) {
            assertNotNull("delay=$delay", playbackLoadControlBuilder(PlaybackOptions(audioDelayMs = delay, cacheSizeMiB = 16)))
        }
    }
}
