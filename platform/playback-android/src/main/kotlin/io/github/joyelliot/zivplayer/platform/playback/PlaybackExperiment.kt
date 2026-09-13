// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import android.content.Context
import android.content.pm.ApplicationInfo
import android.view.View
import android.view.Surface
import io.github.joyelliot.zivplayer.platform.exoplayer.createExoSubtitleView
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoSurfaceOutput
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions

/** Engine switch only: both choices in this branch retain the experimental dependency graph. */
object PlaybackExperiment {
    private data class InstrumentationOverride(
        val exoPlayer: Boolean,
        val options: PlaybackOptions,
        val surfaceSize: Pair<Int, Int>?,
    )
    @Volatile private var instrumentationOverride: InstrumentationOverride? = null

    val usesExoPlayer: Boolean get() = instrumentationOverride?.exoPlayer ?: BuildConfig.EXOPLAYER_EXPERIMENT
    val instrumentationSurfaceSize: Pair<Int, Int>? get() = instrumentationOverride?.surfaceSize

    /** Process-local benchmark input. Call before starting Activity/service in a fresh debug process. */
    fun configureForInstrumentation(
        context: Context,
        exoPlayer: Boolean,
        options: PlaybackOptions,
        surfaceSize: Pair<Int, Int>? = null,
    ) {
        check(BuildConfig.DEBUG && context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            "Playback instrumentation overrides require a debuggable application."
        }
        require(surfaceSize == null || listOf(surfaceSize.first, surfaceSize.second).all { it in 2..4096 && it % 2 == 0 })
        instrumentationOverride = InstrumentationOverride(exoPlayer, options, surfaceSize)
    }

    internal fun playbackOptions(fallback: PlaybackOptions): PlaybackOptions = instrumentationOverride?.options ?: fallback

    fun subtitleView(context: Context): View = createExoSubtitleView(context)
    fun beforeSurfaceDestroyed(surface: Surface) { if (usesExoPlayer) ExoSurfaceOutput.beforeDestroy(surface) }
}
