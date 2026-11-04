// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.exoplayer

import java.nio.ByteBuffer
import kotlin.math.min

/** 正延迟使用等长 FIFO，负延迟丢弃前导帧；始终保持各声道的样本对齐。 */
internal class PcmDelayLine(frameOffset: Long, private val bytesPerFrame: Int, silenceByte: Byte = 0) {
    private var discardFrames = (-frameOffset).coerceAtLeast(0)
    private val history: ByteArray
    private var cursor = 0

    init {
        require(bytesPerFrame > 0 && frameOffset != Long.MIN_VALUE)
        val size = Math.multiplyExact(frameOffset.coerceAtLeast(0), bytesPerFrame.toLong())
        require(size <= MAX_DELAY_BYTES) { "The PCM delay buffer exceeds 64 MiB." }
        history = ByteArray(size.toInt()) { silenceByte }
    }

    /** 返回这次丢弃的输入帧数，调用者据此为提前后的数据重新标记时间。 */
    fun transfer(input: ByteBuffer, output: ByteBuffer): Long {
        require(input.remaining() % bytesPerFrame == 0) { "Incomplete PCM input frame." }
        val discard = min(discardFrames, (input.remaining() / bytesPerFrame).toLong()).toInt()
        input.position(input.position() + discard * bytesPerFrame)
        discardFrames -= discard
        var bytes = min(input.remaining() / bytesPerFrame, output.remaining() / bytesPerFrame) * bytesPerFrame
        if (history.isEmpty()) {
            val originalLimit = input.limit()
            input.limit(input.position() + bytes)
            output.put(input)
            input.limit(originalLimit)
        } else while (bytes > 0) {
            val count = min(bytes, history.size - cursor)
            output.put(history, cursor, count)
            input.get(history, cursor, count)
            cursor = (cursor + count) % history.size
            bytes -= count
        }
        return discard.toLong()
    }

    companion object { const val MAX_DELAY_BYTES = 64L * 1024 * 1024 }
}

internal fun audioDelayFrames(delayMs: Int, sampleRate: Int): Long {
    require(sampleRate > 0)
    return delayMs.toLong() * sampleRate / 1000
}

/** 在 sink 之前同时变换数据和 PTS，避免倍速处理器重新设时钟锚点时抵消偏移。 */
internal class PcmDelayTransport(delayMs: Int, private val sampleRate: Int, private val frameSize: Int,
    silenceByte: Byte = 0, private val trimPrefix: PcmTrimPrefix = PcmTrimPrefix()) {
    private val offsetFrames = audioDelayFrames(delayMs, sampleRate)
    private val line = PcmDelayLine(offsetFrames, frameSize, silenceByte)
    private val output = ByteBuffer.allocateDirect(maxOf(frameSize, 64 * 1024 / frameSize * frameSize))
        .order(java.nio.ByteOrder.LITTLE_ENDIAN)
    private var input: ByteBuffer? = null
    private var inputStart = 0
    private var pending = false
    private var pendingPrefix = false
    private var outputTimeUs = 0L

    fun handle(buffer: ByteBuffer, timeUs: Long, write: (ByteBuffer, Long) -> Boolean): Boolean {
        if (input == null) { input = buffer; inputStart = buffer.position() }
        check(input === buffer) { "The sink must retry the same input until it is consumed." }
        while (true) {
            if (pending) {
                val before = output.position()
                val accepted = write(output, outputTimeUs)
                // 计数以实际下传为准；初始化阻塞后发生 seek 不能提前消耗 priming 余量。
                if (pendingPrefix) trimPrefix.remainingBytes -= output.position() - before
                if (!accepted) return false
                // DefaultAudioSink 在 AudioTrack stall 恢复时可接受并丢弃剩余数据。
                pending = false
            }
            if (!buffer.hasRemaining()) { input = null; return true }
            val consumedFrames = (buffer.position() - inputStart).toLong() / frameSize
            output.clear()
            pendingPrefix = trimPrefix.remainingBytes > 0
            val discardedFrames = if (pendingPrefix) {
                // 正延迟前先原样下传 encoder priming，由默认 sink 裁掉，不写入 FIFO。
                val count = minOf(trimPrefix.remainingBytes, buffer.remaining().toLong(), output.remaining().toLong()).toInt()
                val limit = buffer.limit()
                buffer.limit(buffer.position() + count)
                output.put(buffer)
                buffer.limit(limit)
                0L
            } else line.transfer(buffer, output)
            output.flip()
            outputTimeUs = timeUs + (consumedFrames + discardedFrames + offsetFrames.coerceAtMost(0)) * 1_000_000 / sampleRate
            pending = output.hasRemaining()
        }
    }
}

/** 与默认 sink 的 trimStart 剩余量同行，普通 seek 不重新应用完整 encoderDelay。 */
internal class PcmTrimPrefix(var remainingBytes: Long = 0)
