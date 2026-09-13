// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import android.net.Uri
import android.os.*
import android.view.PixelCopy
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
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
        val report = JSONObject().put("sampleKind", "preview-bitmap-and-main-producer-pixel-changes")
            .put("inputKind", "viewmodel-preview-entry-point")
        // Reflection keeps this exact test APK runnable against the committed libmpv APK.
        val exoObservation = runCatching { Class.forName(
            "io.github.joyelliot.zivplayer.platform.exoplayer.ExoFrameObservation") }.getOrNull()
        fun releasedFrameMs(): Long? = exoObservation?.let { type ->
            onMain { type.getMethod("getPositionMs").invoke(type.getField("INSTANCE").get(null)) as Long }
                ?.takeIf { it >= 0 }
        }
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
            val duration = onMain { controller.duration }
            val wide = args.getString("wideSweep") == "true"
            val fromMs = if (wide) (duration * 0.10).toLong() else 500_000L
            val toMs = if (wide) (duration * 0.90).toLong() else 520_000L
            val commitMs = (fromMs + toMs) / 2
            report.put("durationMs", duration).put("fromMs", fromMs).put("toMs", toMs)
                .put("commitMs", commitMs).put("wideSweep", wide)
            onMain { controller.seekTo(fromMs); controller.play() }
            await { onMain { controller.isPlaying && vm.controller.value?.isPlaying == true &&
                controller.currentPosition in fromMs..fromMs + 2_000 && activity.hasWindowFocus() } }
            SystemClock.sleep(250)
            val surface = onMain { findSurface(activity.window.decorView) }
            checkNotNull(surface)
            report.put("surfaceBufferWidth", onMain { surface.holder.surfaceFrame.width() })
                .put("surfaceBufferHeight", onMain { surface.holder.surfaceFrame.height() })
            val modes = JSONArray()
            for ((name, from, to) in listOf(Triple("forward", fromMs, toMs), Triple("reverse", toMs, fromMs))) {
                val start = SystemClock.elapsedRealtimeNanos()
                val samples = JSONArray()
                val producerSamples = JSONArray()
                var lastHash: Int? = null
                var lastProducerHash: Int? = null
                while (true) {
                    val elapsed = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                    if (elapsed >= 2_000) break
                    val target = from + ((to - from) * elapsed / 2_000).toLong()
                    onMain { vm.previewSeekTo(target) }
                    val previewPixels = onMain {
                        activity.window.decorView.findViewWithTag<ImageView>("seek-preview-frame")
                            ?.takeIf { it.isShown }?.let { it.drawable as? BitmapDrawable }?.bitmap
                            ?.takeUnless(Bitmap::isRecycled)?.let { bitmap ->
                                // Sample the actual preview image. Window PixelCopy also
                                // includes moving controls and can inflate the frame count.
                                val colors = IntArray(96 * 54) { index ->
                                    bitmap.getPixel((index % 96) * bitmap.width / 96,
                                        (index / 96) * bitmap.height / 54)
                                }
                                colors.contentHashCode() to vm.seekPreviewFrame.value?.positionMs
                            }
                    }
                    var producerHash: Int? = null
                    if (surface.holder.surface.isValid) {
                        val latch = CountDownLatch(1)
                        var copied = false
                        PixelCopy.request(surface, pixels, { copied = it == PixelCopy.SUCCESS; latch.countDown() }, Handler(thread.looper))
                        if (latch.await(1, TimeUnit.SECONDS) && copied) {
                            val colors = IntArray(96 * 54); pixels.getPixels(colors, 0, 96, 0, 0, 96, 54)
                            producerHash = colors.contentHashCode()
                            if (producerHash != lastProducerHash) {
                                producerSamples.put(JSONObject().put("elapsedMs", elapsed).put("targetMs", target)
                                    .put("decoderReleasePositionMs", releasedFrameMs()))
                                lastProducerHash = producerHash
                            }
                        }
                    }
                    if (previewPixels != null) {
                            val (hash, previewPosition) = previewPixels
                            if (hash != lastHash) {
                                samples.put(JSONObject().put("elapsedMs", elapsed).put("targetMs", target)
                                    .put("previewStatePositionMs", previewPosition))
                                lastHash = hash
                            }
                    } else if (producerHash != null && producerHash != lastHash) {
                        samples.put(JSONObject().put("elapsedMs", elapsed).put("targetMs", target)
                            .put("decoderReleasePositionMs", releasedFrameMs()))
                        lastHash = producerHash
                    }
                    SystemClock.sleep(12)
                }
                modes.put(JSONObject().put("mode", name).put("pixelChangesPerSecond", samples.length() / 2.0)
                    .put("changedPixels", samples).put("mainSurfaceChangesPerSecond", producerSamples.length() / 2.0)
                    .put("mainSurfaceChanges", producerSamples).put("maxVisibleGapMs", maximumGap(samples))
                    .put("maxMainSurfaceGapMs", maximumGap(producerSamples)))
            }
            report.put("modes", modes)
            report.put("duringDrag", onMain { JSONObject().put("playWhenReady", controller.playWhenReady)
                .put("isPlaying", controller.isPlaying).put("suppressionReason", controller.playbackSuppressionReason) })
            if (args.getString("requireSuppression") != "false") check(onMain {
                controller.playWhenReady && !controller.isPlaying &&
                    controller.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING
            })
            val release = SystemClock.elapsedRealtime()
            onMain { vm.seekTo(commitMs) }
            await { onMain { vm.seekPreviewFrame.value == null && controller.isPlaying && abs(controller.currentPosition - commitMs) < 2_000 } }
            report.put("commitAndResumeMs", SystemClock.elapsedRealtime() - release)
            val first = onMain { controller.currentPosition }
            SystemClock.sleep(700)
            check(onMain { controller.isPlaying && controller.currentPosition > first + 400 })
            exoObservation?.let { type ->
                val state = onMain { type.getMethod("subtitleState").invoke(type.getField("INSTANCE").get(null)) as Map<*, *> }
                report.put("subtitleState", JSONObject(state))
            }
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

    private fun maximumGap(samples: JSONArray): Double {
        var previous = 0.0
        var longest = 0.0
        for (index in 0 until samples.length()) {
            val current = samples.getJSONObject(index).getDouble("elapsedMs")
            longest = maxOf(longest, current - previous)
            previous = current
        }
        return maxOf(longest, 2_000 - previous)
    }
    private fun findSurface(view: View): SurfaceView? = when (view) {
        is SurfaceView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findSurface(view.getChildAt(it)) }
        else -> null
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
