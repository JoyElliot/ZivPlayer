// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import android.os.Handler
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.audio.AudioRendererEventListener
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.MediaCodecAudioRenderer
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector

/** Keeps the default renderer selection and sink, with a targeted FLAC reset workaround. */
internal class ExoRenderersFactory(context: Context) : DefaultRenderersFactory(context) {
    override fun buildAudioRenderers(
        context: Context,
        extensionRendererMode: Int,
        mediaCodecSelector: MediaCodecSelector,
        enableDecoderFallback: Boolean,
        audioSink: AudioSink,
        eventHandler: Handler,
        eventListener: AudioRendererEventListener,
        out: ArrayList<Renderer>,
    ) {
        super.buildAudioRenderers(context, extensionRendererMode, mediaCodecSelector,
            enableDecoderFallback, audioSink, eventHandler, eventListener, out)
        val index = out.indexOfFirst { it.javaClass == MediaCodecAudioRenderer::class.java }
        if (index < 0) return
        out[index] = object : MediaCodecAudioRenderer(context, codecAdapterFactory,
            mediaCodecSelector, enableDecoderFallback, eventHandler, eventListener, audioSink) {
            override fun shouldReleaseCodecInsteadOfFlushing(): Boolean =
                // Some Matroska FLAC streams produce empty PCM buffers after a
                // c2.android.flac.decoder flush. Reinitialization replays codec
                // setup, allowing the audio sink to become ready after a seek.
                codecInfo?.name == "c2.android.flac.decoder" || super.shouldReleaseCodecInsteadOfFlushing()
        }
    }
}
