// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.graphics.Rect
import android.net.Uri
import android.os.*
import android.view.PixelCopy
import android.widget.ImageView
import androidx.lifecycle.ViewModelProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.platform.playback.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/** Opt-in existing SAF source; a unique unregistered ID prevents personal-history writes. */
class PersonalScrubBenchmarkTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test fun mainPlayerPreviewPixelsAndResume() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("seekBenchmark") == "true")
        val context = instrumentation.targetContext
        val sourceFile = File(context.cacheDir, "seek-benchmark-source.json")
        assumeTrue(sourceFile.exists())
        val uri = Uri.parse(JSONObject(sourceFile.readText()).getString("source_uri"))
        val app = context.applicationContext as ZivPlayerApplication
        val id = "zivplayer-personal-scrub-${UUID.randomUUID()}"
        check(runBlocking { app.mediaRepositories.recent.findById(MediaId(id)) } == null)
        val activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
            .filterIsInstance<MainActivity>().first() }
        val vm = onMain { ViewModelProvider(activity)[PlaybackControllerViewModel::class.java] }
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        val pixels = Bitmap.createBitmap(96, 54, Bitmap.Config.ARGB_8888)
        val thread = HandlerThread("PersonalScrubPixels").apply { start() }
        val report = JSONObject().put("sampleKind", "composited-preview-pixel-changes")
            .put("inputKind", "viewmodel-preview-entry-point")
        try {
            onMain {
                controller.setMediaItem(MediaItem.Builder().setMediaId(id).setUri(uri)
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Private scrub benchmark")
                        .setExtras(Bundle().apply {
                            putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, PlaybackRequestSequencer.next())
                        }).build()).build())
                controller.prepare()
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true)
            }
            await { onMain { controller.playbackState == Player.STATE_READY && controller.duration > 600_000 } }
            onMain { controller.seekTo(500_000); controller.play() }
            await { onMain { controller.isPlaying && vm.controller.value?.isPlaying == true &&
                controller.currentPosition in 500_000..502_000 && activity.hasWindowFocus() } }
            SystemClock.sleep(250)
            val modes = JSONArray()
            for ((name, from, to) in listOf(Triple("forward", 500_000L, 520_000L),
                    Triple("reverse", 520_000L, 500_000L))) {
                val start = SystemClock.elapsedRealtimeNanos()
                val samples = JSONArray()
                var lastHash: Int? = null
                while (true) {
                    val elapsed = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                    if (elapsed >= 2_000) break
                    val target = from + ((to - from) * elapsed / 2_000).toLong()
                    onMain { vm.previewSeekTo(target) }
                    val rect = onMain {
                        activity.window.decorView.findViewWithTag<ImageView>("seek-preview-frame")
                            ?.takeIf { it.isShown && it.drawable != null }?.let {
                                val location = IntArray(2); it.getLocationInWindow(location)
                                Rect(location[0], location[1], location[0] + it.width, location[1] + it.height)
                            }
                    }
                    if (rect != null && !rect.isEmpty) {
                        val latch = CountDownLatch(1)
                        var copied = false
                        PixelCopy.request(activity.window, rect, pixels, {
                            copied = it == PixelCopy.SUCCESS; latch.countDown()
                        }, Handler(thread.looper))
                        if (latch.await(1, TimeUnit.SECONDS) && copied) {
                            val colors = IntArray(96 * 54); pixels.getPixels(colors, 0, 96, 0, 0, 96, 54)
                            val hash = colors.contentHashCode()
                            if (hash != lastHash) {
                                samples.put(JSONObject().put("elapsedMs", elapsed).put("targetMs", target)
                                    .put("previewStatePositionMs", onMain { vm.seekPreviewFrame.value?.positionMs }))
                                lastHash = hash
                            }
                        }
                    }
                    SystemClock.sleep(12)
                }
                modes.put(JSONObject().put("mode", name).put("pixelChangesPerSecond", samples.length() / 2.0)
                    .put("changedPixels", samples))
            }
            report.put("modes", modes)
            report.put("duringDrag", onMain { JSONObject().put("playWhenReady", controller.playWhenReady)
                .put("isPlaying", controller.isPlaying).put("suppressionReason", controller.playbackSuppressionReason) })
            if (args.getString("requireSuppression") != "false") check(onMain {
                controller.playWhenReady && !controller.isPlaying &&
                    controller.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING
            })
            val release = SystemClock.elapsedRealtime()
            onMain { vm.seekTo(510_000) }
            await { onMain { vm.seekPreviewFrame.value == null && controller.isPlaying && abs(controller.currentPosition - 510_000) < 2_000 } }
            report.put("commitAndResumeMs", SystemClock.elapsedRealtime() - release)
            val first = onMain { controller.currentPosition }
            SystemClock.sleep(700)
            check(onMain { controller.isPlaying && controller.currentPosition > first + 400 })
            check(runBlocking { app.mediaRepositories.recent.findById(MediaId(id)) } == null)
            instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
                try { File(context.cacheDir, "personal-scrub-resumed.png").outputStream().use {
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
                } } finally { bitmap.recycle() }
            }
        } finally {
            File(context.cacheDir, "personal-scrub-benchmark.json").writeText(report.toString(2))
            onMain { vm.cancelSeekPreview(); controller.stop(); controller.release() }
            thread.quitSafely()
            pixels.recycle()
        }
    }

    private fun await(predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 20_000
        while (SystemClock.elapsedRealtime() < deadline) { if (predicate()) return; SystemClock.sleep(20) }
        error("Personal scrub benchmark timed out")
    }
    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
