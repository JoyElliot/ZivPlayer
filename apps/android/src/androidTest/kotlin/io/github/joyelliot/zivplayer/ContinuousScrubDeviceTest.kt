// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Rect
import android.net.Uri
import android.os.*
import android.view.*
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
import io.github.joyelliot.zivplayer.platform.playback.*
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/** Opt-in: real Choreographer and composited window pixels, without Compose Test's frame clock. */
class ContinuousScrubDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private lateinit var activity: MainActivity
    private lateinit var pixels: Bitmap
    private lateinit var pixelThread: HandlerThread

    @Test fun continuousVisibleFramesAndResume() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("seekBenchmark") == "true")
        val arguments = InstrumentationRegistry.getArguments()
        val playingDuringDrag = arguments.getString("playingDuringDrag") == "true"
        val requireSuppression = arguments.getString("requireSuppression") != "false"
        val startMs = arguments.getString("dragStartMs")?.toLong() ?: 20_000L
        val spanMs = arguments.getString("dragSpanMs")?.toLong() ?: 20_000L
        require(startMs >= 0 && spanMs > 0 && startMs + spanMs < 90_000)
        activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
            .filterIsInstance<MainActivity>().first() }
        pixels = Bitmap.createBitmap(640, 360, Bitmap.Config.ARGB_8888)
        pixelThread = HandlerThread("ContinuousScrubPixels").apply { start() }
        val context = instrumentation.targetContext
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        val vm = onMain { ViewModelProvider(activity)[PlaybackControllerViewModel::class.java] }
        val report = JSONObject().put("playingDuringDrag", playingDuringDrag).put("dragStartMs", startMs)
            .put("dragSpanMs", spanMs)
        try {
            onMain {
                controller.setMediaItem(MediaItem.Builder().setMediaId("zivplayer-seek-preview-fixture")
                    .setUri(Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/seek-preview.mp4"))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Seek frame fixture").setExtras(Bundle().apply {
                        putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, PlaybackRequestSequencer.next())
                    }).build()).build())
                controller.prepare()
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true)
            }
            await { onMain { controller.playbackState == Player.STATE_READY && activity.window.decorView.width > activity.window.decorView.height } }
            onMain { controller.play() }
            await { onMain { controller.isPlaying && controller.currentPosition > 500 } }
            onMain { controller.pause(); controller.seekTo(startMs) }
            await { frameMs()?.let { abs(it - startMs) < 100 } == true }
            if (playingDuringDrag) {
                onMain { controller.play() }
                // The input belongs to the app's controller, not this test client's
                // optimistic mask. Wait for both to observe the same play intent.
                await { onMain { controller.isPlaying && vm.controller.value?.isPlaying == true } }
            }
            await { onMain { activity.hasWindowFocus() } }
            SystemClock.sleep(250)
            val bounds = onMain { val decor = activity.window.decorView; decor.width to decor.height }
            val x = bounds.first * (if (spanMs <= 40_000) .35f else .06f)
            val end = x + bounds.first * (spanMs / 90_000f)
            val y = bounds.second * .3f
            val gestureOrigin = onMain { controller.currentPosition }
            report.put("gestureOriginMs", gestureOrigin)
            val down = SystemClock.uptimeMillis()
            touch(MotionEvent.ACTION_DOWN, x, y, down)
            fun drag(name: String, from: Float, to: Float): JSONObject {
                val start = SystemClock.elapsedRealtimeNanos()
                val samples = JSONArray()
                var last: Long? = null
                while (true) {
                    val elapsed = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                    if (elapsed >= 2_000) break
                    touch(MotionEvent.ACTION_MOVE, from + (to - from) * (elapsed / 2_000).toFloat(), y, down)
                    val frame = frameMs()
                    if (frame != null && frame != last) {
                        samples.put(JSONObject().put("elapsedMs", (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0)
                            .put("frameMs", frame)); last = frame
                    }
                    SystemClock.sleep(12)
                }
                touch(MotionEvent.ACTION_MOVE, to, y, down)
                return JSONObject().put("mode", name).put("elapsedMs", (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0)
                    .put("changedFrames", samples).put("visibleFps", samples.length() / 2.0)
            }
            val forward = drag("forward", x, end)
            val reverse = drag("reverse", end, x)
            report.put("forward", forward).put("reverse", reverse)
            report.put("duringDrag", onMain { JSONObject().put("playWhenReady", controller.playWhenReady)
                .put("isPlaying", controller.isPlaying).put("suppressionReason", controller.playbackSuppressionReason)
                .put("mainPositionMs", controller.currentPosition) })
            if (requireSuppression) check(onMain { !controller.isPlaying && controller.playWhenReady == playingDuringDrag &&
                controller.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING })
            touch(MotionEvent.ACTION_UP, x, y, down)
            await { !previewVisible() && frameMs()?.let { abs(it - gestureOrigin) < if (playingDuringDrag) 1_200 else 150 } == true }
            report.put("committedFrameMs", frameMs())
            check(onMain { controller.playWhenReady == playingDuringDrag })
            if (spanMs <= 20_000) {
                check(forward.getDouble("visibleFps") >= 10) { "Forward frames too sparse: $forward" }
                check(reverse.getDouble("visibleFps") >= 15) { "Reverse frames too sparse: $reverse" }
            }
            onMain { controller.play() }
            SystemClock.sleep(700)
            val target = onMain { val value = controller.currentPosition + 5_000; vm.seekBy(5_000); value }
            val start = SystemClock.elapsedRealtimeNanos()
            val samples = JSONArray()
            var last: Long? = null
            while ((SystemClock.elapsedRealtimeNanos() - start) / 1_000_000 < 1_600) {
                val frame = frameMs()
                if (frame != null && frame != last) {
                    samples.put(JSONObject().put("elapsedMs", (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0)
                        .put("frameMs", frame)); last = frame
                }
                SystemClock.sleep(8)
            }
            report.put("playingSeek", JSONObject().put("targetMs", target).put("changedFrames", samples))
            check(onMain { controller.isPlaying })
            check((0 until samples.length()).count { samples.getJSONObject(it).getLong("frameMs") >= target - 100 } >= 15)
        } finally {
            File(context.cacheDir, "seek-continuous-ui.json").writeText(report.toString(2))
            onMain { controller.stop(); controller.release() }
            pixelThread.quitSafely()
            pixels.recycle()
        }
    }

    private fun previewVisible() = onMain { activity.window.decorView.findViewWithTag<ImageView>("seek-preview-frame")
        ?.let { it.isShown && it.drawable != null } == true }
    private fun frameMs(): Long? {
        val source = onMain {
            fun video(view: View): View? = when (view) {
                is TextureView, is SurfaceView -> view
                is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { video(view.getChildAt(it)) }
                else -> null
            }
            val image = activity.window.decorView.findViewWithTag<ImageView>("seek-preview-frame")
            val view = image?.takeIf { it.isShown && it.drawable != null } ?: video(activity.window.decorView) ?: return@onMain null
            val location = IntArray(2); view.getLocationInWindow(location)
            view to Rect(location[0], location[1], location[0] + view.width, location[1] + view.height)
        } ?: return null
        val (view, rect) = source
        if (rect.isEmpty) return null
        val latch = CountDownLatch(1)
        var copied = false
        val listener = PixelCopy.OnPixelCopyFinishedListener { copied = it == PixelCopy.SUCCESS; latch.countDown() }
        // A Window copy includes the app preview overlay, but not a separately composited
        // SurfaceView. Main-video samples therefore read that producer's Surface directly.
        if (view is SurfaceView) PixelCopy.request(view, pixels, listener, Handler(pixelThread.looper))
        else PixelCopy.request(activity.window, rect, pixels, listener, Handler(pixelThread.looper))
        if (!latch.await(1, TimeUnit.SECONDS) || !copied) return null
        val contentWidth = minOf(640f, 640f * rect.height() / rect.width() * 16f / 9)
        val offset = (640 - contentWidth) / 2
        fun white(x: Float): Boolean {
            val pixel = pixels.getPixel((offset + x / 1280 * contentWidth).toInt().coerceIn(0, 639), 212)
            return Color.red(pixel) > 170 && Color.green(pixel) > 170 && Color.blue(pixel) > 170
        }
        if (!white(1184f)) return null
        var code = 0
        for (bit in 0..11) if (white(92f + 88 * bit)) code = code or (1 shl bit)
        return (code * 1000L / 24).takeIf { it < 90_000 }
    }
    private fun touch(action: Int, x: Float, y: Float, down: Long) = onMain {
        val event = MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, x, y, 0)
        try { activity.window.decorView.dispatchTouchEvent(event) } finally { event.recycle() }
    }
    private fun await(predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10_000
        while (SystemClock.elapsedRealtime() < deadline) { if (predicate()) return; SystemClock.sleep(20) }
        error("Timed out; frameMs=${frameMs()}")
    }
    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
