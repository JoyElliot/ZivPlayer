// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.common.C
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.source.MediaPeriod
import org.junit.Assert.*
import org.junit.Test
import java.lang.reflect.Proxy

class SubtitleOffsetPeriodTest {
    private fun child(handle: (String, Array<out Any?>) -> Any?): MediaPeriod = Proxy.newProxyInstance(
        MediaPeriod::class.java.classLoader, arrayOf(MediaPeriod::class.java)) { _, method, arguments ->
        handle(method.name, arguments ?: emptyArray())
    } as MediaPeriod

    @Test fun advancingSubtitlesSeeksTheirOwnSourceAheadWithoutMovingVideo() {
        var requested = -1L
        val period = SubtitleOffsetPeriod(child { name, args ->
            assertEquals("seekToUs", name); requested = args[0] as Long; 99_000L
        }, -60_000_000)
        assertEquals(10_000_000L, period.seekToUs(10_000_000))
        assertEquals(70_000_000L, requested)
    }

    @Test fun positiveDelayAtTheHeadClampsOnlyTheTextSource() {
        val period = SubtitleOffsetPeriod(child { name, args ->
            assertEquals("seekToUs", name); assertEquals(0L, args[0]); 0L
        }, 5_000_000)
        assertEquals(1_000_000L, period.seekToUs(1_000_000))
        assertEquals(1_000_000L, period.getAdjustedSeekPositionUs(1_000_000, SeekParameters.EXACT))
    }

    @Test fun textDiscontinuitiesCannotForceAnAudioVideoSeek() {
        var consumed = false
        val period = SubtitleOffsetPeriod(child { name, _ ->
            assertEquals("readDiscontinuity", name); consumed = true; 0L
        }, 2_000_000)
        assertEquals(C.TIME_UNSET, period.readDiscontinuity())
        assertTrue(consumed)
    }

    @Test fun bufferedAndLoadingPositionsUseThePlayerTimelineAndPreserveEos() {
        var position = 65_000_000L
        val period = SubtitleOffsetPeriod(child { _, _ -> position }, -60_000_000)
        assertEquals(5_000_000L, period.bufferedPositionUs)
        assertEquals(5_000_000L, period.nextLoadPositionUs)
        position = C.TIME_END_OF_SOURCE
        assertEquals(C.TIME_END_OF_SOURCE, period.bufferedPositionUs)
        assertEquals(C.TIME_END_OF_SOURCE, period.nextLoadPositionUs)
        position = C.TIME_UNSET
        assertEquals(C.TIME_UNSET, period.bufferedPositionUs)
    }

    @Test fun callbackIdentityBelongsToTheWrapper() {
        lateinit var childCallback: MediaPeriod.Callback
        val child = child { name, args ->
            assertEquals("prepare", name)
            childCallback = args[0] as MediaPeriod.Callback
            assertEquals(8_000_000L, args[1]); null
        }
        val period = SubtitleOffsetPeriod(child, -3_000_000)
        var prepared = false
        var loading = false
        period.prepare(object : MediaPeriod.Callback {
            override fun onPrepared(mediaPeriod: MediaPeriod) { assertSame(period, mediaPeriod); prepared = true }
            override fun onContinueLoadingRequested(source: MediaPeriod) { assertSame(period, source); loading = true }
        }, 5_000_000)
        childCallback.onPrepared(child)
        childCallback.onContinueLoadingRequested(child)
        assertTrue(prepared && loading)
    }

    @Test fun allSentinelsRemainSentinelsInBothDirections() {
        for (sentinel in listOf(C.TIME_UNSET, C.TIME_END_OF_SOURCE)) for (offset in listOf(-60_000_000L, 60_000_000L)) {
            assertEquals(sentinel, subtitleShiftTime(sentinel, offset))
            assertEquals(sentinel, subtitleChildPosition(sentinel, offset))
        }
        assertEquals(-500_000L, subtitleShiftTime(500_000, -1_000_000))
    }
}
