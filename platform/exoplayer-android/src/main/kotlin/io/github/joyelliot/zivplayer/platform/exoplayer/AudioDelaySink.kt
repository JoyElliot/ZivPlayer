// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.Util
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioOffloadSupport
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.ForwardingAudioSink
import java.nio.ByteBuffer

internal class DelayRenderersFactory(context: Context, private val audioDelayMs: Int) : DefaultRenderersFactory(context) {
    override fun buildAudioSink(context: Context, enableFloatOutput: Boolean,
        enableAudioOutputPlaybackParams: Boolean): AudioSink? {
        val sink = super.buildAudioSink(context, false, enableAudioOutputPlaybackParams) ?: return null
        return if (audioDelayMs == 0) sink else AudioDelaySink(sink, audioDelayMs)
    }
}

/** 非零偏移走解码后的 PCM；默认零偏移完全使用 Media3 原始 sink。 */
internal class AudioDelaySink(sink: AudioSink, private val delayMs: Int) : ForwardingAudioSink(sink) {
    private var format: Format? = null
    private var transport: PcmDelayTransport? = null
    private var trimPrefix = PcmTrimPrefix()

    override fun supportsFormat(format: Format) = getFormatSupport(format) != AudioSink.SINK_FORMAT_UNSUPPORTED
    override fun getFormatSupport(format: Format): Int =
        if (format.sampleMimeType == MimeTypes.AUDIO_RAW && Util.isEncodingLinearPcm(format.pcmEncoding))
            super.getFormatSupport(format) else AudioSink.SINK_FORMAT_UNSUPPORTED
    override fun getFormatOffloadSupport(format: Format): AudioOffloadSupport = AudioOffloadSupport.DEFAULT_UNSUPPORTED
    override fun setOffloadMode(offloadMode: Int) = super.setOffloadMode(AudioSink.OFFLOAD_MODE_DISABLED)
    override fun setSkipSilenceEnabled(skipSilenceEnabled: Boolean) = super.setSkipSilenceEnabled(false)

    override fun configure(audioSinkConfig: AudioSink.AudioSinkConfig) {
        val next = audioSinkConfig.format
        if (next.sampleMimeType != MimeTypes.AUDIO_RAW || !Util.isEncodingLinearPcm(next.pcmEncoding) ||
            next.sampleRate <= 0 || next.channelCount <= 0 ||
            audioDelayFrames(delayMs.coerceAtLeast(0), next.sampleRate) *
                Util.getPcmFrameSize(next.pcmEncoding, next.channelCount) > PcmDelayLine.MAX_DELAY_BYTES) {
            throw AudioSink.ConfigurationException("Unsupported PCM delay format or buffer size.", next)
        }
        super.configure(audioSinkConfig)
        // 默认 trimmer 在每次 configure（包括相同 Format）后重新应用 encoderDelay。
        format = next
        transport = null
        trimPrefix = PcmTrimPrefix(if (delayMs > 0) next.encoderDelay.coerceAtLeast(0).toLong() *
            Util.getPcmFrameSize(next.pcmEncoding, next.channelCount) else 0)
    }

    override fun handleBuffer(buffer: ByteBuffer, presentationTimeUs: Long, encodedAccessUnitCount: Int): Boolean {
        val format = checkNotNull(format)
        val current = transport ?: PcmDelayTransport(delayMs, format.sampleRate,
            Util.getPcmFrameSize(format.pcmEncoding, format.channelCount),
            if (format.pcmEncoding == C.ENCODING_PCM_8BIT) 0x80.toByte() else 0, trimPrefix).also { transport = it }
        return current.handle(buffer, presentationTimeUs) { output, timeUs ->
            super.handleBuffer(output, timeUs, encodedAccessUnitCount)
        }
    }

    // 提前后的输入 PTS 与 renderer 所见 PTS 不同，改用常规调度，避免高估已缓冲时长。
    override fun getAudioTrackBufferSizeUs(): Long = if (delayMs < 0) C.TIME_UNSET else super.getAudioTrackBufferSizeUs()
    override fun handleDiscontinuity() { transport = null; super.handleDiscontinuity() }
    override fun flush() { transport = null; super.flush() }
    override fun reset() { transport = null; format = null; super.reset() }
    override fun release() { transport = null; format = null; super.release() }
}
