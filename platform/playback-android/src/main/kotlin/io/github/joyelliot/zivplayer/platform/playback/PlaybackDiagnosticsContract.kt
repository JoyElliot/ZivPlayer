// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import android.os.Bundle
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics

/** Same-UID custom commands. Missing keys represent unavailable measurements. */
object PlaybackDiagnosticsContract {
    const val ACTION_READ = "io.github.joyelliot.zivplayer.READ_DIAGNOSTICS"
    const val ACTION_SETTINGS_STATUS = "io.github.joyelliot.zivplayer.SETTINGS_STATUS"
    const val SETTINGS_MESSAGE = "settingsMessage"

    fun encode(value: PlaybackDiagnostics): Bundle = Bundle().apply {
        value.videoCodec?.let { putString("videoCodec", it) }
        value.audioCodec?.let { putString("audioCodec", it) }
        value.decoder?.let { putString("decoder", it) }
        value.colorPrimaries?.let { putString("colorPrimaries", it) }
        value.transferFunction?.let { putString("transferFunction", it) }
        value.pixelFormat?.let { putString("pixelFormat", it) }
        value.engineVersion?.let { putString("engineVersion", it) }
        value.ffmpegVersion?.let { putString("ffmpegVersion", it) }
        value.videoWidth?.let { putInt("videoWidth", it) }
        value.videoHeight?.let { putInt("videoHeight", it) }
        value.droppedFrames?.let { putLong("droppedFrames", it) }
        value.decoderDroppedFrames?.let { putLong("decoderDroppedFrames", it) }
        value.cacheUsedBytes?.let { putLong("cacheUsedBytes", it) }
        value.videoBitrate?.let { putLong("videoBitrate", it) }
        value.audioBitrate?.let { putLong("audioBitrate", it) }
        value.sourceFramesPerSecond?.let { putDouble("sourceFramesPerSecond", it) }
        value.displayFramesPerSecond?.let { putDouble("displayFramesPerSecond", it) }
        value.cacheDurationSeconds?.let { putDouble("cacheDurationSeconds", it) }
        value.audioVideoSyncSeconds?.let { putDouble("audioVideoSyncSeconds", it) }
    }
    fun decode(bundle: Bundle): PlaybackDiagnostics = PlaybackDiagnostics(
        videoCodec = if (bundle.containsKey("videoCodec")) bundle.getString("videoCodec") else null,
        audioCodec = if (bundle.containsKey("audioCodec")) bundle.getString("audioCodec") else null,
        decoder = if (bundle.containsKey("decoder")) bundle.getString("decoder") else null,
        colorPrimaries = if (bundle.containsKey("colorPrimaries")) bundle.getString("colorPrimaries") else null,
        transferFunction = if (bundle.containsKey("transferFunction")) bundle.getString("transferFunction") else null,
        pixelFormat = if (bundle.containsKey("pixelFormat")) bundle.getString("pixelFormat") else null,
        engineVersion = if (bundle.containsKey("engineVersion")) bundle.getString("engineVersion") else null,
        ffmpegVersion = if (bundle.containsKey("ffmpegVersion")) bundle.getString("ffmpegVersion") else null,
        videoWidth = if (bundle.containsKey("videoWidth")) bundle.getInt("videoWidth") else null,
        videoHeight = if (bundle.containsKey("videoHeight")) bundle.getInt("videoHeight") else null,
        droppedFrames = if (bundle.containsKey("droppedFrames")) bundle.getLong("droppedFrames") else null,
        decoderDroppedFrames = if (bundle.containsKey("decoderDroppedFrames")) bundle.getLong("decoderDroppedFrames") else null,
        cacheUsedBytes = if (bundle.containsKey("cacheUsedBytes")) bundle.getLong("cacheUsedBytes") else null,
        videoBitrate = if (bundle.containsKey("videoBitrate")) bundle.getLong("videoBitrate") else null,
        audioBitrate = if (bundle.containsKey("audioBitrate")) bundle.getLong("audioBitrate") else null,
        sourceFramesPerSecond = if (bundle.containsKey("sourceFramesPerSecond")) bundle.getDouble("sourceFramesPerSecond") else null,
        displayFramesPerSecond = if (bundle.containsKey("displayFramesPerSecond")) bundle.getDouble("displayFramesPerSecond") else null,
        cacheDurationSeconds = if (bundle.containsKey("cacheDurationSeconds")) bundle.getDouble("cacheDurationSeconds") else null,
        audioVideoSyncSeconds = if (bundle.containsKey("audioVideoSyncSeconds")) bundle.getDouble("audioVideoSyncSeconds") else null,
    )
}
