// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.settings

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import io.github.joyelliot.zivplayer.designsystem.*
import java.util.Locale

@Composable
fun DiagnosticsScreen(snapshot: PlaybackDiagnostics?, device: String, message: String?,
    onBack: () -> Unit, onExport: () -> Unit) {
    ZivScreen(stringResource(R.string.settings_diagnostics)) {
        ZivPrimaryButton(stringResource(R.string.diagnostics_back), onBack)
        ZivText(device)
        ZivText(stringResource(R.string.diagnostics_hint))
        message?.let { ZivStatusText(it) }
        val missing = stringResource(R.string.diagnostics_unavailable)
        fun number(value: Double?, suffix: String = ""): String? = value?.takeIf(Double::isFinite)?.let {
            String.format(Locale.getDefault(), "%.3f%s", it, suffix)
        }
        val data = snapshot ?: PlaybackDiagnostics()
        listOf(
            "mpv" to data.engineVersion, "FFmpeg" to data.ffmpegVersion,
            stringResource(R.string.diagnostics_video_codec) to data.videoCodec,
            stringResource(R.string.diagnostics_audio_codec) to data.audioCodec,
            stringResource(R.string.diagnostics_decoder) to data.decoder,
            stringResource(R.string.diagnostics_resolution) to data.videoWidth?.let { w -> data.videoHeight?.let { h -> "$w × $h" } },
            stringResource(R.string.diagnostics_source_fps) to number(data.sourceFramesPerSecond, " fps"),
            stringResource(R.string.diagnostics_display_fps) to number(data.displayFramesPerSecond, " Hz"),
            stringResource(R.string.diagnostics_dropped) to data.droppedFrames?.toString(),
            stringResource(R.string.diagnostics_decoder_dropped) to data.decoderDroppedFrames?.toString(),
            stringResource(R.string.diagnostics_cache_bytes) to number(data.cacheUsedBytes?.div(1048576.0), " MiB"),
            stringResource(R.string.diagnostics_cache_time) to number(data.cacheDurationSeconds, " s"),
            stringResource(R.string.diagnostics_av_sync) to number(data.audioVideoSyncSeconds?.times(1000), " ms"),
            stringResource(R.string.diagnostics_pixel_format) to data.pixelFormat,
            stringResource(R.string.diagnostics_color_primaries) to data.colorPrimaries,
            stringResource(R.string.diagnostics_transfer) to data.transferFunction,
            stringResource(R.string.diagnostics_video_bitrate) to number(data.videoBitrate?.div(1000.0), " kbit/s"),
            stringResource(R.string.diagnostics_audio_bitrate) to number(data.audioBitrate?.div(1000.0), " kbit/s"),
        ).forEach { (label, value) -> ZivText("$label · ${value ?: missing}") }
        ZivPrimaryButton(stringResource(R.string.diagnostics_export), onExport, enabled = snapshot != null)
    }
}
