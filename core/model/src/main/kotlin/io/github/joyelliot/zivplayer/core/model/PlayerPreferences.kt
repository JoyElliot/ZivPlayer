// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.model

enum class AppearanceMode { SYSTEM, LIGHT, DARK }
enum class DecoderMode { AUTOMATIC, SOFTWARE }
enum class RenderQuality { EFFICIENT, BALANCED, HIGH }
enum class VideoFit { FIT, FILL, STRETCH }
enum class DeinterlaceMode { OFF, AUTOMATIC, ON }
enum class ToneMappingMode { AUTOMATIC, BT2390, HABLE, CLIP }
enum class SubtitleColor { WHITE, YELLOW, GREEN }

/** Identifies an imported app-owned resource, never an arbitrary path or provider URI. */
@JvmInline
value class PlaybackResourceId(val value: String) {
    init { require(value.matches(Regex("[a-f0-9]{64}"))) { "Invalid playback resource identity." } }
}

class ShaderChain(ids: List<PlaybackResourceId> = emptyList()) {
    val ids: List<PlaybackResourceId> = ids.toList()
    init { require(ids.size <= 8 && ids.distinct().size == ids.size) { "Select at most eight distinct shaders." } }
    override fun equals(other: Any?): Boolean = other is ShaderChain && ids == other.ids
    override fun hashCode(): Int = ids.hashCode()
}

/** Portable playback intent. Native option names and imported resource paths belong to the adapter. */
data class PlaybackOptions(
    val decoder: DecoderMode = DecoderMode.AUTOMATIC,
    val quality: RenderQuality = RenderQuality.BALANCED,
    val videoFit: VideoFit = VideoFit.FIT,
    val deinterlace: DeinterlaceMode = DeinterlaceMode.OFF,
    val toneMapping: ToneMappingMode = ToneMappingMode.AUTOMATIC,
    val audioDelayMs: Int = 0,
    val subtitleDelayMs: Int = 0,
    val subtitleScalePercent: Int = 100,
    val subtitlePositionPercent: Int = 100,
    val subtitleBorderSize: Int = 3,
    val subtitleColor: SubtitleColor = SubtitleColor.WHITE,
    val overrideStyledSubtitles: Boolean = false,
    val subtitleFont: PlaybackResourceId? = null,
    val subtitleFontFamily: String? = null,
    val shaders: ShaderChain = ShaderChain(),
    val cacheSizeMiB: Int = 128,
) {
    init {
        require(audioDelayMs in -10_000..10_000)
        require(subtitleDelayMs in -60_000..60_000)
        require(subtitleScalePercent in 50..300)
        require(subtitlePositionPercent in 0..100)
        require(subtitleBorderSize in 0..6)
        require(cacheSizeMiB in 16..512)
        require(subtitleFontFamily == null ||
            (subtitleFontFamily.isNotBlank() && subtitleFontFamily.length <= 160 &&
                subtitleFontFamily.none(Char::isISOControl)))
        require((subtitleFont == null) == (subtitleFontFamily == null))
    }
}

data class PlayerPreferences(
    val appearance: AppearanceMode = AppearanceMode.SYSTEM,
    val resumePlayback: Boolean = true,
    val backgroundPlayback: Boolean = true,
    val keepScreenOn: Boolean = true,
    val autoPictureInPicture: Boolean = false,
    val rememberTrackSelection: Boolean = true,
    val defaultPlaybackRate: PlaybackRatePermille = PlaybackRatePermille.NORMAL,
    val seekStepSeconds: Int = 10,
    val options: PlaybackOptions = PlaybackOptions(),
) {
    init { require(seekStepSeconds in 5..60) }
}

/** Unavailable measurements remain null. Diagnostics never use zero as a substitute for missing data. */
data class PlaybackDiagnostics(
    val videoCodec: String? = null,
    val audioCodec: String? = null,
    val decoder: String? = null,
    val videoWidth: Int? = null,
    val videoHeight: Int? = null,
    val sourceFramesPerSecond: Double? = null,
    val displayFramesPerSecond: Double? = null,
    val droppedFrames: Long? = null,
    val decoderDroppedFrames: Long? = null,
    val cacheUsedBytes: Long? = null,
    val cacheDurationSeconds: Double? = null,
    val audioVideoSyncSeconds: Double? = null,
    val colorPrimaries: String? = null,
    val transferFunction: String? = null,
    val pixelFormat: String? = null,
    val videoBitrate: Long? = null,
    val audioBitrate: Long? = null,
    val engineVersion: String? = null,
    val ffmpegVersion: String? = null,
)
