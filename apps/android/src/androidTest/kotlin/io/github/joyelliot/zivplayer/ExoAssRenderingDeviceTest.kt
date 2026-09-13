// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.platform.playback.*
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.concurrent.TimeUnit

/** Checks actual transparent libass GL output, including redraw while paused and hiding the track. */
class ExoAssRenderingDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test fun styledAssPixelsAndPausedTrackDisable() {
        assumeTrue(PlaybackExperiment.usesExoPlayer)
        val context = instrumentation.targetContext
        val activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
            .filterIsInstance<MainActivity>().first() }
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        val report = JSONObject()
        fun diagnostics() {
            val type = Class.forName("io.github.joyelliot.zivplayer.platform.exoplayer.ExoFrameObservation")
            val state = onMain { type.getMethod("subtitleState").invoke(type.getField("INSTANCE").get(null)) as Map<*, *> }
            report.put("subtitleState", JSONObject(state))
            report.put("selectedTracks", onMain { controller.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }
                .map { "${it.getTrackFormat(0).id}: ${it.isSelected}" }.joinToString() })
        }
        try {
            onMain {
                controller.setMediaItem(MediaItem.Builder().setMediaId("zivplayer-exo-ass-fixture")
                    .setUri(Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/tracks-subtitles.mkv"))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("ExoPlayer libass render check")
                        .setExtras(Bundle().apply {
                            putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, PlaybackRequestSequencer.next())
                        }).build()).build(), 2_000)
                controller.prepare()
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true)
            }
            await { onMain { controller.playbackState == Player.STATE_READY && controller.currentTracks.groups.any { it.type == C.TRACK_TYPE_TEXT } } }
            onMain {
                val first = controller.currentTracks.groups.first { it.type == C.TRACK_TYPE_TEXT }
                controller.trackSelectionParameters = controller.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(TrackSelectionOverride(first.mediaTrackGroup, 0)).build()
            }
            await { onMain { controller.currentTracks.groups.first { it.type == C.TRACK_TYPE_TEXT }.isSelected } }
            await { onMain { findOverlay(activity.window.decorView) != null } }
            val overlay = onMain { checkNotNull(findOverlay(activity.window.decorView)) }
            fun opaquePixels(): Int = onMain {
                overlay.bitmap?.let { bitmap ->
                    try {
                        val colors = IntArray(bitmap.width * bitmap.height)
                        bitmap.getPixels(colors, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                        colors.count { it ushr 24 > 20 }
                    } finally { bitmap.recycle() }
                } ?: 0
            }
            diagnostics()
            await { opaquePixels() > 250 }
            report.put("visibleAssPixels", opaquePixels())
            onMain {
                val bitmap = checkNotNull(overlay.bitmap)
                try {
                    var xSum = 0L; var ySum = 0L; var count = 0; var bright = 0
                    for (y in 0 until bitmap.height) for (x in 0 until bitmap.width) {
                        val color = bitmap.getPixel(x, y)
                        if (color ushr 24 > 20) { xSum += x; ySum += y; count++ }
                        if (color ushr 24 > 200 && (color shr 16 and 255) > 200 && (color shr 8 and 255) > 200 && (color and 255) > 200) bright++
                    }
                    val centerX = xSum.toDouble() / count / bitmap.width
                    val centerY = ySum.toDouble() / count / bitmap.height
                    report.put("assNormalizedCenterX", centerX).put("assNormalizedCenterY", centerY)
                    report.put("brightAssPixels", bright)
                    check(bright > 1000) { "The fixture's white ASS fill must remain white: $bright" }
                    check(centerX in 0.35..0.65 && centerY in 0.8..1.0) { "ASS placement must match the bottom-centered fixture: $centerX,$centerY" }
                } finally { bitmap.recycle() }
            }
            instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
                try { File(context.cacheDir, "exo-ass-visible.png").outputStream().use {
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
                } } finally { bitmap.recycle() }
            }
            onMain { controller.seekTo(5_000) }
            await { onMain { controller.currentPosition in 4_900..5_100 } }
            await {
                onMain {
                    overlay.bitmap?.let { bitmap ->
                        try {
                            val pixels = IntArray(bitmap.width * bitmap.height)
                            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                            val yellow = pixels.count { it ushr 24 > 150 && (it shr 16 and 255) > 180 && (it shr 8 and 255) > 180 && (it and 255) < 100 }
                            report.put("yellowAssPixels", yellow)
                            yellow > 500
                        } finally { bitmap.recycle() }
                    } ?: false
                }
            }
            instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
                try { File(context.cacheDir, "exo-ass-chinese.png").outputStream().use {
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
                } } finally { bitmap.recycle() }
            }
            onMain { controller.trackSelectionParameters = controller.trackSelectionParameters.buildUpon()
                .clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build() }
            await { onMain { controller.currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected } } }
            await { opaquePixels() == 0 }
            report.put("hiddenAssPixels", opaquePixels())
            report.put("loadedPlaybackLibraries", File("/proc/self/maps").readLines().map { it.substringAfterLast('/') }
                .filter { it in setOf("libmpv.so", "libzivplayer_mpv.so", "libass.so", "libasskt.so") }.distinct().sorted().joinToString())
            report.put("nativeExecutableApkOffsets", org.json.JSONArray(File("/proc/self/maps").readLines()
                .filter { it.contains(context.applicationInfo.sourceDir) && it.contains("r-xp") }
                .map { it.trim().split(Regex("\\s+"))[2] }))
        } catch (failure: Throwable) {
            val type = Class.forName("io.github.joyelliot.zivplayer.platform.exoplayer.ExoFrameObservation")
            report.put("nativeRaster", onMain { type.getMethod("subtitleRasterProbe").invoke(type.getField("INSTANCE").get(null)) })
            throw failure
        } finally {
            diagnostics()
            File(context.cacheDir, "exo-ass-rendering.json").writeText(report.toString(2))
            onMain { controller.stop(); controller.release() }
        }
    }

    private fun findOverlay(view: View): TextureView? = when {
        view is TextureView && view.javaClass.simpleName == "AssSubtitleTextureView" -> view
        view is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findOverlay(view.getChildAt(it)) }
        else -> null
    }
    private fun await(predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 12_000
        while (SystemClock.elapsedRealtime() < deadline) { if (predicate()) return; SystemClock.sleep(30) }
        error("libass output did not reach the expected state")
    }
    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
