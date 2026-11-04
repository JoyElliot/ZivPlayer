// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.util.Consumer
import androidx.media3.extractor.text.CuesWithTiming
import androidx.media3.extractor.text.SubtitleParser
import org.junit.Assert.assertEquals
import org.junit.Test

class EndMarkedSubtitleParserTest {
    @Test fun finalMarkerFollowsTheCueSpanningInitialSeekAndPrecedesDiscardedCues() {
        val current = CuesWithTiming(emptyList(), 8_000_000, 3_500_000)
        val old = CuesWithTiming(emptyList(), 1_000_000, 1_000_000)
        val output = mutableListOf<CuesWithTiming>()
        EndMarkedSubtitleParser(parser(listOf(current, old))).parse(byteArrayOf(),
            SubtitleParser.OutputOptions.cuesAfterThenRemainingCuesBefore(9_500_000)) { output += it }
        assertEquals(listOf(8_000_000L, 11_500_000L, 1_000_000L), output.map { it.startTimeUs })
        assertEquals(0L, output[1].durationUs)
    }

    @Test fun unknownDurationIsNotCutOffByAnArtificialEnd() {
        val cues = listOf(CuesWithTiming(emptyList(), 1_000_000, C.TIME_UNSET))
        val output = mutableListOf<CuesWithTiming>()
        EndMarkedSubtitleParser(parser(cues)).parse(byteArrayOf(), SubtitleParser.OutputOptions.allCues()) { output += it }
        assertEquals(cues, output)
    }

    private fun parser(cues: List<CuesWithTiming>) = object : SubtitleParser {
        override fun getCueReplacementBehavior() = Format.CUE_REPLACEMENT_BEHAVIOR_REPLACE
        override fun parse(data: ByteArray, offset: Int, length: Int, outputOptions: SubtitleParser.OutputOptions,
            output: Consumer<CuesWithTiming>) = cues.forEach(output::accept)
    }
}
