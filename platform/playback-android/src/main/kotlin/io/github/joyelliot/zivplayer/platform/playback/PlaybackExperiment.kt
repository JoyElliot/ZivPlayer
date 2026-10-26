// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import android.content.Context
import android.content.pm.ApplicationInfo
import android.view.View
import android.view.Surface
import android.view.SurfaceView
import io.github.joyelliot.zivplayer.platform.exoplayer.createExoSubtitleView
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoSurfaceOutput
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.platform.libmpv.MpvVideoOutput
import io.github.joyelliot.zivplayer.platform.libvlc.VlcSurfaceOutput

/** Engine switch only: all three choices retain this branch's experimental dependency graph. */
object PlaybackExperiment {
    private data class InstrumentationOverride(
        val exoPlayer: Boolean,
        val options: PlaybackOptions,
        val surfaceSize: Pair<Int, Int>?,
        val libVlc: Boolean,
        val mpvVideoOutput: MpvVideoOutput,
    )
    @Volatile private var instrumentationOverride: InstrumentationOverride? = null

    val usesExoPlayer: Boolean get() = instrumentationOverride?.exoPlayer ?: BuildConfig.EXOPLAYER_EXPERIMENT
    val usesLibVlc: Boolean get() = instrumentationOverride?.libVlc ?: BuildConfig.LIBVLC4_EXPERIMENT
    val usesNativeScrubPreview: Boolean get() = usesExoPlayer || usesLibVlc
    val instrumentationSurfaceSize: Pair<Int, Int>? get() = instrumentationOverride?.surfaceSize
    val mpvVideoOutput: MpvVideoOutput get() = instrumentationOverride?.mpvVideoOutput ?: MpvVideoOutput.GPU_NEXT

    /** Process-local benchmark input. Call before starting Activity/service in a fresh debug process. */
    fun configureForInstrumentation(
        context: Context,
        exoPlayer: Boolean,
        options: PlaybackOptions,
        surfaceSize: Pair<Int, Int>? = null,
        libVlc: Boolean = false,
        mpvVideoOutput: MpvVideoOutput = MpvVideoOutput.GPU_NEXT,
    ) {
        check(BuildConfig.DEBUG && context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            "Playback instrumentation overrides require a debuggable application."
        }
        require(surfaceSize == null || listOf(surfaceSize.first, surfaceSize.second).all { it in 2..4096 && it % 2 == 0 })
        require(!libVlc || !exoPlayer) { "Select exactly one instrumentation playback engine." }
        instrumentationOverride = InstrumentationOverride(exoPlayer, options, surfaceSize, libVlc, mpvVideoOutput)
    }

    internal fun playbackOptions(fallback: PlaybackOptions): PlaybackOptions = instrumentationOverride?.options ?: fallback

    fun subtitleView(context: Context): View = createExoSubtitleView(context)
    fun registerVideoSurfaces(video: Surface, subtitles: SurfaceView?, onLayout: (Int, Int, Int, Int, Int, Int) -> Unit) {
        if (usesLibVlc && subtitles != null) VlcSurfaceOutput.register(video, subtitles, onLayout)
    }
    fun beforeSurfaceDestroyed(surface: Surface) {
        if (usesExoPlayer) ExoSurfaceOutput.beforeDestroy(surface)
        if (usesLibVlc) VlcSurfaceOutput.beforeDestroy(surface)
    }
}
