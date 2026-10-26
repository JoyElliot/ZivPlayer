// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer

import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.os.*
import android.view.*
import android.widget.FrameLayout
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.media3.common.*
import androidx.media3.common.text.CueGroup
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/** Plain packaged Media3 control without ZivPlayer backend or the libass extension. */
class NativeExoControlTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    @Test fun nativeSubtitleSeekControl() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.containsKey("nativeExoMode"))
        val mode = args.getString("nativeExoMode")!!.also { require(it in setOf("linear", "seek", "toggle-seek")) }
        val index = args.getString("nativeExoTextIndex", "1").toInt().also { require(it in 0..1) }
        val label = args.getString("nativeExoLabel")!!.also { require(it.matches(Regex("[a-z0-9-]{1,80}"))) }
        val fixture = File(context.filesDir, "power-fixtures/clarity-subtitles.mkv")
        val hash = MessageDigest.getInstance("SHA-256").digest(fixture.readBytes()).joinToString("") { "%02x".format(it) }
        check(hash == args.getString("nativeExoFixtureSha256"))
        val output = File(context.filesDir, "native-exo-control-results/$label")
        check(!output.exists() && output.mkdirs())
        val report = JSONObject().put("label", label).put("mode", mode).put("textIndex", index)
            .put("fixtureSha256", hash).put("path", "Plain Media3 ExoPlayer + SubtitleView; no app backend/core or libass extension")
        val cues = JSONArray()
        report.put("cues", cues)
        var player: Player? = null
        var activity: MainActivity? = null
        var lastCueTexts: List<String> = emptyList()
        try {
            DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
            await(15000) { activity = resumedActivity(); activity != null }
            val active = checkNotNull(activity)
            onMain { active.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
                active.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
            lateinit var root: FrameLayout
            lateinit var video: SurfaceView
            lateinit var subtitleView: View
            onMain {
                root = FrameLayout(active)
                video = SurfaceView(active)
                subtitleView = Class.forName("androidx.media3.ui.SubtitleView")
                    .getConstructor(android.content.Context::class.java).newInstance(active) as View
                for (view in listOf(video, subtitleView)) root.addView(view, FrameLayout.LayoutParams(1920, 1080, Gravity.CENTER))
                active.setContentView(root)
                WindowInsetsControllerCompat(active.window, root).hide(WindowInsetsCompat.Type.systemBars())
            }
            await(15000) { onMain { active.hasWindowFocus() && root.width == 2400 && root.height == 1080 && video.holder.surface.isValid } }
            onMain {
                val builder = Class.forName("androidx.media3.exoplayer.ExoPlayer\$Builder")
                    .getConstructor(android.content.Context::class.java).newInstance(context)
                val p = builder.javaClass.getMethod("build").invoke(builder) as Player
                player = p
                p.addListener(object : Player.Listener {
                    override fun onCues(cueGroup: CueGroup) {
                        lastCueTexts = cueGroup.cues.map { it.text?.toString().orEmpty() }
                        cues.put(JSONObject().put("positionMs", p.currentPosition).put("texts", JSONArray(lastCueTexts)))
                        subtitleView.javaClass.getMethod("setCues", List::class.java).invoke(subtitleView, cueGroup.cues)
                    }
                })
                p.setVideoSurface(video.holder.surface)
                p.volume = 0f
                p.setMediaItem(MediaItem.fromUri(android.net.Uri.fromFile(fixture)))
                p.prepare()
                p.play()
            }
            val p = checkNotNull(player)
            await(15000) { onMain { p.playerError?.let { throw it }
                p.currentTracks.groups.count { it.type == C.TRACK_TYPE_TEXT } == 2 && p.currentPosition >= 300 } }
            onMain {
                val group = p.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }[index]
                report.put("textFormat", group.getTrackFormat(0).toString())
                p.trackSelectionParameters = p.trackSelectionParameters.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0)).build()
            }
            if (mode == "linear" || mode == "toggle-seek") await(15000) { onMain { p.currentPosition >= 5000 } }
            if (mode == "toggle-seek") {
                onMain { p.pause(); p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                    .clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build() }
                SystemClock.sleep(500)
                onMain {
                    val group = p.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }[index]
                    p.trackSelectionParameters = p.trackSelectionParameters.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                        .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0)).build()
                    p.play()
                }
                SystemClock.sleep(400)
            }
            onMain { p.pause(); if (mode != "linear") p.seekTo(5000) }
            await(15000) { onMain { p.playerError?.let { throw it }; p.playbackState == Player.STATE_READY && !p.isPlaying } }
            SystemClock.sleep(2000)
            onMain {
                report.put("positionMs", p.currentPosition).put("isPlaying", p.isPlaying)
                    .put("visibleCueTexts", JSONArray(lastCueTexts)).put("completed", true)
            }
            val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
            try { File(output, "subtitle-on.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
            finally { bitmap.recycle() }
        } catch (error: Throwable) {
            report.put("failure", error.toString())
            throw error
        } finally {
            onMain { player?.release(); activity?.finish() }
            File(output, "report.json").writeText(report.toString(2))
        }
    }
    private fun resumedActivity(): MainActivity? = onMain {
        ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).filterIsInstance<MainActivity>().firstOrNull()
    }
    private fun await(timeout: Long, predicate: () -> Boolean) {
        val end = SystemClock.elapsedRealtime() + timeout
        while (SystemClock.elapsedRealtime() < end) { if (predicate()) return; SystemClock.sleep(50) }
        error("Native Exo readiness timeout")
    }
    private fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(block) }
        return checkNotNull(result).getOrThrow()
    }
}
