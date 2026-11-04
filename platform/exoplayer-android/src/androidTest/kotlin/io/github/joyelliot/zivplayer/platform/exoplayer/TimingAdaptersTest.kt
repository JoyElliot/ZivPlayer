// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackParameters
import androidx.media3.decoder.DecoderInputBuffer
import androidx.media3.exoplayer.FormatHolder
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.source.SampleStream
import androidx.media3.exoplayer.source.MediaPeriod
import androidx.media3.exoplayer.source.ClippingMediaPeriod
import org.junit.Assert.*
import org.junit.Test
import java.lang.reflect.Proxy
import java.nio.ByteBuffer

class TimingAdaptersTest {
    @Test fun audioSpeedAndPauseKeepTheDelayLineButSeekingResetsIt() {
        val received = mutableListOf<Pair<Long, List<Byte>>>()
        val delegate = Proxy.newProxyInstance(AudioSink::class.java.classLoader, arrayOf(AudioSink::class.java)) { _, method, args ->
            when (method.name) {
                "handleBuffer" -> {
                    val buffer = args[0] as ByteBuffer
                    received += (args[1] as Long) to ByteArray(buffer.remaining()).also { buffer.get(it) }.toList()
                    true
                }
                "getFormatSupport" -> AudioSink.SINK_FORMAT_SUPPORTED_DIRECTLY
                else -> null
            }
        } as AudioSink
        val sink = AudioDelaySink(delegate, 2)
        val format = Format.Builder().setSampleMimeType(MimeTypes.AUDIO_RAW).setPcmEncoding(C.ENCODING_PCM_16BIT)
            .setSampleRate(1000).setChannelCount(1).build()
        sink.configure(AudioSink.AudioSinkConfig.Builder(format).build())
        assertFalse(sink.supportsFormat(format.buildUpon().setSampleMimeType(MimeTypes.AUDIO_AAC).build()))
        assertTrue(sink.handleBuffer(ByteBuffer.wrap(byteArrayOf(1, 2, 3, 4)), 0, 1))
        sink.setPlaybackParameters(PlaybackParameters(2f))
        sink.pause(); sink.play()
        assertTrue(sink.handleBuffer(ByteBuffer.wrap(byteArrayOf(5, 6, 7, 8)), 2000, 1))
        sink.flush()
        assertTrue(sink.handleBuffer(ByteBuffer.wrap(byteArrayOf(9, 10, 11, 12)), 500_000, 1))
        assertEquals(listOf(0L to listOf<Byte>(0, 0, 0, 0), 2000L to listOf<Byte>(1, 2, 3, 4),
            500_000L to listOf<Byte>(0, 0, 0, 0)), received)
    }

    @Test fun subtitleSamplesAndAbsoluteFormatsShiftButRelativeFormatsAndEosKeepTheirMeaning() {
        var format = Format.Builder().setSampleMimeType(MimeTypes.TEXT_VTT).setSubsampleOffsetUs(0).build()
        var result = C.RESULT_FORMAT_READ
        var eos = false
        var seek = -1L
        val child = object : SampleStream {
            override fun isReady() = true
            override fun maybeThrowError() = Unit
            override fun skipData(positionUs: Long): Int { seek = positionUs; return 0 }
            override fun readData(holder: FormatHolder, buffer: DecoderInputBuffer, flags: Int): Int {
                if (result == C.RESULT_FORMAT_READ) holder.format = format
                else {
                    buffer.timeUs = 2_000_000
                    if (eos) buffer.addFlag(C.BUFFER_FLAG_END_OF_STREAM)
                }
                return result
            }
        }
        val stream = SubtitleOffsetStream(child, -1_000_000)
        val holder = FormatHolder()
        val buffer = DecoderInputBuffer(DecoderInputBuffer.BUFFER_REPLACEMENT_MODE_NORMAL)
        assertEquals(C.RESULT_FORMAT_READ, stream.readData(holder, buffer, 0))
        assertEquals(-1_000_000L, holder.format!!.subsampleOffsetUs)
        format = format.buildUpon().setSubsampleOffsetUs(Format.OFFSET_SAMPLE_RELATIVE).build()
        stream.readData(holder, buffer, 0)
        assertEquals(Format.OFFSET_SAMPLE_RELATIVE, holder.format!!.subsampleOffsetUs)
        result = C.RESULT_BUFFER_READ
        stream.readData(holder, buffer, SampleStream.FLAG_PEEK)
        assertEquals(1_000_000L, buffer.timeUs)
        eos = true
        stream.readData(holder, buffer, 0)
        assertEquals(2_000_000L, buffer.timeUs)
        stream.skipData(5_000_000)
        assertEquals(6_000_000L, seek)
    }

    @Test fun delayedSubtitleBeyondVideoEndBecomesEosDuringPeek() {
        val childStream = object : SampleStream {
            override fun isReady() = true
            override fun maybeThrowError() = Unit
            override fun skipData(positionUs: Long) = 0
            override fun readData(holder: FormatHolder, buffer: DecoderInputBuffer, flags: Int): Int {
                buffer.timeUs = 9_000_000
                return C.RESULT_BUFFER_READ
            }
        }
        val delayed = SubtitleOffsetStream(childStream, 60_000_000)
        val childPeriod = Proxy.newProxyInstance(MediaPeriod::class.java.classLoader, arrayOf(MediaPeriod::class.java)) { _, method, args ->
            when (method.name) {
                "selectTracks" -> {
                    @Suppress("UNCHECKED_CAST") val streams = args[2] as Array<SampleStream?>
                    streams[0] = delayed
                    args[4]
                }
                "getBufferedPositionUs" -> C.TIME_END_OF_SOURCE
                else -> null
            }
        } as MediaPeriod
        val clipping = ClippingMediaPeriod(childPeriod, false, 0, 10_000_000)
        val streams = arrayOfNulls<SampleStream>(1)
        clipping.selectTracks(arrayOf(null), booleanArrayOf(false), streams, booleanArrayOf(false), 0)
        val buffer = DecoderInputBuffer(DecoderInputBuffer.BUFFER_REPLACEMENT_MODE_NORMAL)
        assertEquals(C.RESULT_BUFFER_READ, streams[0]!!.readData(FormatHolder(), buffer, SampleStream.FLAG_PEEK))
        assertTrue(buffer.isEndOfStream)
    }
}
