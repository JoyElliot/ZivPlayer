// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.model.*

/** Resolved app-private resources. Callers never submit arbitrary native option names. */
class LibmpvResourcePaths(subtitleFontsDirectory: String? = null, shaderPaths: List<String> = emptyList()) {
    val subtitleFontsDirectory = subtitleFontsDirectory
    val shaderPaths = shaderPaths.toList()
    init {
        require(shaderPaths.size <= 8)
        (shaderPaths + listOfNotNull(subtitleFontsDirectory)).forEach {
            require(it.startsWith('/') && it.none { c -> c == ':' || c.isISOControl() }) { "Resources must use app-private absolute paths." }
        }
    }
}

interface LibmpvConfigurationPort {
    suspend fun configure(options: PlaybackOptions, resources: LibmpvResourcePaths)
    suspend fun readDiagnostics(): PlaybackDiagnostics
}

internal data class MpvPlaybackConfiguration(val options: PlaybackOptions = PlaybackOptions(),
    val resources: LibmpvResourcePaths = LibmpvResourcePaths()) {
    init { require(options.shaders.ids.size == resources.shaderPaths.size) }

    fun properties(): Map<String, String> = linkedMapOf(
        "hwdec" to if (options.decoder == DecoderMode.SOFTWARE) "no" else "mediacodec,mediacodec-copy",
        "scale" to when (options.quality) {
            RenderQuality.EFFICIENT -> "bilinear"
            RenderQuality.BALANCED -> "spline36"
            RenderQuality.HIGH -> "ewa_lanczossharp"
        },
        "cscale" to if (options.quality == RenderQuality.HIGH) "spline36" else "bilinear",
        "dscale" to if (options.quality == RenderQuality.EFFICIENT) "bilinear" else "mitchell",
        "correct-downscaling" to if (options.quality == RenderQuality.HIGH) "yes" else "no",
        "keepaspect" to if (options.videoFit == VideoFit.STRETCH) "no" else "yes",
        "panscan" to if (options.videoFit == VideoFit.FILL) "1" else "0",
        "deinterlace" to when (options.deinterlace) {
            DeinterlaceMode.OFF -> "no"
            DeinterlaceMode.AUTOMATIC -> "auto"
            DeinterlaceMode.ON -> "yes"
        },
        "tone-mapping" to when (options.toneMapping) {
            ToneMappingMode.AUTOMATIC -> "auto"
            ToneMappingMode.BT2390 -> "bt.2390"
            ToneMappingMode.HABLE -> "hable"
            ToneMappingMode.CLIP -> "clip"
        },
        "audio-delay" to (options.audioDelayMs / 1000.0).toString(),
        "sub-delay" to (options.subtitleDelayMs / 1000.0).toString(),
        "sub-scale" to (options.subtitleScalePercent / 100.0).toString(),
        "sub-pos" to options.subtitlePositionPercent.toString(),
        "sub-outline-size" to options.subtitleBorderSize.toString(),
        "sub-color" to when (options.subtitleColor) {
            SubtitleColor.WHITE -> "#FFFFFFFF"
            SubtitleColor.YELLOW -> "#FFFFFF00"
            SubtitleColor.GREEN -> "#FF66FF66"
        },
        "sub-ass-override" to if (options.overrideStyledSubtitles) "force" else "no",
        "sub-font" to (options.subtitleFontFamily ?: "sans-serif"),
        "sub-fonts-dir" to resources.subtitleFontsDirectory.orEmpty(),
        "glsl-shaders" to resources.shaderPaths.joinToString(":"),
        "demuxer-max-bytes" to (options.cacheSizeMiB * 1024L * 1024L).toString(),
        // Backward seeking gets a separate bounded budget; the UI names the forward budget.
        "demuxer-max-back-bytes" to (options.cacheSizeMiB * 1024L * 1024L / 4).toString(),
    )
}

/** Slow-changing strings use the existing primitive JNI observation surface. */
internal class MpvDiagnosticsObserver {
    private val strings = mutableMapOf<String, String>()
    fun start(player: MpvClient) {
        STRING_PROPERTIES.forEachIndexed { index, property ->
            player.observeProperty(property, MpvPropertyFormat.STRING, FIRST_TOKEN + index)
        }
    }
    fun onProperty(change: MpvPropertyChange): Boolean {
        val index = change.replyUserdata?.minus(FIRST_TOKEN)?.takeIf { it >= 0 && it < STRING_PROPERTIES.size }?.toInt() ?: return false
        val property = STRING_PROPERTIES[index]
        if (property == change.name) {
            val value = (change.value as? MpvPropertyValue.StringValue)?.value?.take(512)?.takeIf(String::isNotBlank)
            if (value == null) strings.remove(property) else strings[property] = value
        }
        return true
    }
    fun clearMedia() { strings.keys.retainAll(setOf("mpv-version", "ffmpeg-version")) }
    fun snapshot(player: MpvClient?, mediaLoaded: Boolean): PlaybackDiagnostics {
        fun number(name: String): Double? = if (!mediaLoaded || player == null) null
            else runCatching { player.getPropertyDouble(name)?.takeIf(Double::isFinite) }.getOrNull()
        fun count(name: String) = number(name)?.takeIf { it >= 0 && it < Long.MAX_VALUE.toDouble() }?.toLong()
        fun string(name: String) = strings[name].takeIf { mediaLoaded }
        return PlaybackDiagnostics(
            videoCodec = string("video-codec"), audioCodec = string("audio-codec"), decoder = string("hwdec-current"),
            videoWidth = count("video-params/w")?.takeIf { it in 1..32768 }?.toInt(),
            videoHeight = count("video-params/h")?.takeIf { it in 1..32768 }?.toInt(),
            sourceFramesPerSecond = number("container-fps"), displayFramesPerSecond = number("display-fps"),
            droppedFrames = count("frame-drop-count"), decoderDroppedFrames = count("decoder-frame-drop-count"),
            cacheUsedBytes = count("demuxer-cache-state/total-bytes"), cacheDurationSeconds = number("demuxer-cache-duration"),
            audioVideoSyncSeconds = number("avsync"), colorPrimaries = string("video-params/primaries"),
            transferFunction = string("video-params/gamma"), pixelFormat = string("video-params/pixelformat"),
            videoBitrate = count("video-bitrate"), audioBitrate = count("audio-bitrate"),
            engineVersion = strings["mpv-version"], ffmpegVersion = strings["ffmpeg-version"],
        )
    }
    private companion object {
        const val FIRST_TOKEN = 10L
        val STRING_PROPERTIES = listOf("video-codec", "audio-codec", "hwdec-current", "video-params/primaries",
            "video-params/gamma", "video-params/pixelformat", "mpv-version", "ffmpeg-version")
    }
}
