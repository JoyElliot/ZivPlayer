// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.exoplayer

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer

class PcmDelayLineTest {
    private fun run(offset: Long, frameSize: Int, chunks: List<ByteArray>, outputSize: Int = 4): List<Byte> {
        val line = PcmDelayLine(offset, frameSize)
        val collected = mutableListOf<Byte>()
        chunks.forEach { data ->
            val input = ByteBuffer.wrap(data)
            while (input.hasRemaining()) {
                val output = ByteBuffer.allocate(outputSize)
                line.transfer(input, output)
                collected += output.array().take(output.position())
            }
        }
        return collected
    }

    @Test fun positiveDelayPreservesLengthAcrossChunksAndTruncatesTheTail() {
        assertEquals(listOf<Byte>(0, 0, 0, 0, 1, 2, 3, 4, 5, 6),
            run(2, 2, listOf(byteArrayOf(1, 2), byteArrayOf(3, 4, 5, 6), byteArrayOf(7, 8, 9, 10))))
    }

    @Test fun negativeDelayDiscardsAcrossInputChunksWithoutMovingChannels() {
        assertEquals(listOf<Byte>(9, 10, 11, 12),
            run(-2, 4, listOf(byteArrayOf(1, 2, 3, 4), byteArrayOf(5, 6, 7, 8, 9, 10, 11, 12))))
        assertTrue(run(-100, 2, listOf(byteArrayOf(1, 2))).isEmpty())
    }

    @Test fun zeroDelayIsByteExactAndAnEmptyStreamStaysEmpty() {
        val data = ByteArray(128) { it.toByte() }
        assertEquals(data.toList(), run(0, 4, listOf(data), 12))
        assertTrue(run(50, 2, listOf(byteArrayOf())).isEmpty())
    }

    @Test fun rateConversionDoesNotOverflowAtHighSampleRates() {
        assertEquals(7_680_000L, audioDelayFrames(10_000, 768_000))
        assertEquals(-44L, audioDelayFrames(-1, 44_100))
    }

    @Test fun positiveDelayUsesUnsignedSilenceForEightBitPcm() {
        val input = ByteBuffer.wrap(byteArrayOf(10, 11, 12))
        val output = ByteBuffer.allocate(3)
        PcmDelayLine(2, 1, 0x80.toByte()).transfer(input, output)
        assertArrayEquals(byteArrayOf(0x80.toByte(), 0x80.toByte(), 10), output.array())
    }

    @Test fun negativeTransportRewritesTimestampsAsWellAsSamples() {
        val transport = PcmDelayTransport(-3, 1000, 2)
        val packets = mutableListOf<Pair<Long, List<Byte>>>()
        val writer: (ByteBuffer, Long) -> Boolean = { output, pts ->
            val bytes = ByteArray(output.remaining()).also { output.get(it) }
            packets += pts to bytes.toList(); true
        }
        assertTrue(transport.handle(ByteBuffer.wrap(byteArrayOf(1, 2, 3, 4)), 50_000, writer))
        assertTrue(transport.handle(ByteBuffer.wrap(byteArrayOf(5, 6, 7, 8, 9, 10)), 52_000, writer))
        assertTrue(transport.handle(ByteBuffer.wrap(byteArrayOf(11, 12)), 55_000, writer))
        assertEquals(listOf(50_000L to listOf<Byte>(7, 8, 9, 10), 52_000L to listOf<Byte>(11, 12)), packets)
    }

    @Test fun backpressureRetriesOneTransformedBufferWithoutDuplicatingDelayOrSamples() {
        val transport = PcmDelayTransport(1, 1000, 2)
        val input = ByteBuffer.wrap(byteArrayOf(1, 2, 3, 4, 5, 6))
        val bytes = mutableListOf<Byte>()
        var previous: ByteBuffer? = null
        val writer: (ByteBuffer, Long) -> Boolean = { output, pts ->
            assertEquals(25_000L, pts)
            if (previous != null) assertSame(previous, output)
            previous = output
            repeat(minOf(2, output.remaining())) { bytes += output.get() }
            !output.hasRemaining()
        }
        assertFalse(transport.handle(input, 25_000, writer))
        assertFalse(transport.handle(input, 25_000, writer))
        assertTrue(transport.handle(input, 25_000, writer))
        assertEquals(listOf<Byte>(0, 0, 1, 2, 3, 4), bytes)
    }

    @Test fun largeInputIsChunkedWithContiguousPresentationTimes() {
        val transport = PcmDelayTransport(5, 1000, 2)
        val input = ByteBuffer.wrap(ByteArray(200_000) { (it % 127 + 1).toByte() })
        var nextTime = 1_000_000L
        var total = 0
        assertTrue(transport.handle(input, nextTime) { output, pts ->
            assertEquals(nextTime, pts)
            val size = output.remaining()
            nextTime += size / 2 * 1000
            total += size
            output.position(output.limit()); true
        })
        assertEquals(200_000, total)
    }

    @Test fun encoderPrimingIsTrimmedBeforePositiveDelayAndNeverLeaksFromTheFifo() {
        val priming = PcmTrimPrefix(2)
        val transport = PcmDelayTransport(3, 1000, 1, trimPrefix = priming)
        var trimRemaining = 2
        val audible = mutableListOf<Byte>()
        assertTrue(transport.handle(ByteBuffer.wrap(byteArrayOf(99, 98, 1, 2, 3, 4, 5, 6)), 0) { output, _ ->
            while (output.hasRemaining()) {
                val byte = output.get()
                if (trimRemaining > 0) trimRemaining-- else audible += byte
            }
            true
        })
        assertEquals(listOf<Byte>(0, 0, 0, 1, 2, 3), audible)
        assertEquals(0L, priming.remainingBytes)
    }

    @Test fun seekingBeforePrimingReachesTheSinkPreservesOnlyTheUnconsumedPrefix() {
        val priming = PcmTrimPrefix(4)
        val first = PcmDelayTransport(3, 1000, 2, trimPrefix = priming)
        assertFalse(first.handle(ByteBuffer.wrap(byteArrayOf(9, 9, 8, 8, 1, 1)), 0) { output, _ ->
            output.position(output.position() + 2); false
        })
        assertEquals(2L, priming.remainingBytes)
        // flush 丢弃原 pending buffer；余下的两字节仍须先交默认 sink 裁剪。
        val afterSeek = PcmDelayTransport(3, 1000, 2, trimPrefix = priming)
        val output = mutableListOf<Byte>()
        assertTrue(afterSeek.handle(ByteBuffer.wrap(byteArrayOf(7, 7, 6, 6, 5, 5)), 1_000_000) { buffer, _ ->
            while (buffer.hasRemaining()) output += buffer.get()
            true
        })
        assertEquals(listOf<Byte>(7, 7, 0, 0, 0, 0), output)
        assertEquals(0L, priming.remainingBytes)
    }

    @Test fun downstreamMayAcceptAndDropUnconsumedDataDuringStallRecovery() {
        val transport = PcmDelayTransport(1, 1000, 2)
        assertTrue(transport.handle(ByteBuffer.wrap(byteArrayOf(1, 2, 3, 4)), 0) { _, _ -> true })
        val received = mutableListOf<Byte>()
        assertTrue(transport.handle(ByteBuffer.wrap(byteArrayOf(5, 6)), 2000) { buffer, _ ->
            while (buffer.hasRemaining()) received += buffer.get()
            true
        })
        assertEquals(listOf<Byte>(3, 4), received)
    }
}
