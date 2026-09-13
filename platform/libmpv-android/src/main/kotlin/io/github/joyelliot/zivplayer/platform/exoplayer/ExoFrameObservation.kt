// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.exoplayer

/** Experiment evidence only: decoder release metadata, not a screen presentation assertion. */
object ExoFrameObservation {
    @Volatile var positionMs: Long = -1
        internal set
    @Volatile var releaseSerial: Long = 0
        internal set
    @Volatile var lastError: String? = null
        internal set

    fun subtitleState(): Map<String, String> {
        val handler = ExoSubtitleOutput.state.value.handler
        return mapOf("handler" to (handler != null).toString(),
            "hasTracks" to handler?.hasTracks().toString(), "selected" to (handler?.track != null).toString(),
            "renderer" to (handler?.render != null).toString(), "videoTimeUs" to handler?.videoTime.toString(),
            "eventTimesMs" to handler?.track?.getEvents()?.take(5)?.joinToString { "${it.start}+${it.duration}" }.toString(),
            "videoSize" to handler?.videoSize.toString(), "surfaceSize" to handler?.surfaceSize.toString(),
            "overlayBound" to (handler?.videoTimeCallback != null).toString())
    }

    /** Destructive to the renderer's change flag; call only after a failed device probe. */
    fun subtitleRasterProbe(): String {
        val handler = ExoSubtitleOutput.state.value.handler ?: return "no-handler"
        val frame = handler.render?.renderFrame(handler.videoTime / 1000,
            io.github.peerless2012.ass.AssTexType.BITMAP_RGBA) ?: return "no-frame"
        val description = "changed=${frame.changed}; images=${frame.images?.size}; sizes=" +
            frame.images?.joinToString { "${it.w}x${it.h}@${it.x},${it.y} color=${it.color.toUInt().toString(16)}" }
        frame.images?.forEach { it.bitmap?.recycle() }
        return description
    }
}
