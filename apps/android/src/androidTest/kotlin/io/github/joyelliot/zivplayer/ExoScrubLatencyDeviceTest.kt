// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.graphics.Color
import android.os.*
import android.view.*
import android.view.accessibility.AccessibilityNodeInfo
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
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoPlayerBackend
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoSurfaceOutput
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoFrameObservation
import io.github.joyelliot.zivplayer.platform.playback.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.lang.ref.WeakReference
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/** Independent input and PixelCopy clocks measure actual encoded frames while the finger is down. */
class ExoScrubLatencyDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private lateinit var activity: MainActivity
    private lateinit var surface: SurfaceView
    private lateinit var pixels: Bitmap
    private lateinit var pixelThread: HandlerThread
    private var privateMedia = false
    private var producerHash = 0

    @Test fun targetFollowingAndHeldFrameLatency() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("seekBenchmark") == "true")
        val native = args.getString("nativeScrubReference") == "true"
        privateMedia = args.getString("privateSeekSource") == "true"
        val context = instrumentation.targetContext
        val source = if (privateMedia) JSONObject(File(context.cacheDir, "seek-benchmark-source.json").readText())
            .getString("source_uri") else "content://io.github.joyelliot.zivplayer.test.fixtures/seek-preview.mp4"
        val origin = if (privateMedia) 500_000L else 12_000L
        val app = context.applicationContext as ZivPlayerApplication
        val id = "latency-${UUID.randomUUID()}"
        check(runBlocking { app.mediaRepositories.recent.findById(MediaId(id)) } == null)
        activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
            .filterIsInstance<MainActivity>().first() }
        val vm = onMain { ViewModelProvider(activity)[PlaybackControllerViewModel::class.java] }
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        pixels = Bitmap.createBitmap(320, 180, Bitmap.Config.ARGB_8888)
        pixelThread = HandlerThread("ScrubLatencyPixels").apply { start() }
        val report = JSONObject().put("inputKind", if (native) "native-exoplayer-reference" else "real-slider-motion-events")
            .put("frameEvidence", if (privateMedia) "Decoder-release PTS plus PixelCopy hash; pixel identity is not encoded"
                else "PixelCopy source-frame barcode; not optical scanout")
        var backend: ExoPlayerBackend? = null
        var stopInput: (() -> Unit)? = null
        var pointerDown = false
        val down = SystemClock.uptimeMillis()
        var touchX = 0f
        var touchY = 0f
        try {
            onMain {
                controller.setMediaItem(MediaItem.Builder().setMediaId(id)
                    .setUri(source)
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Scrub latency fixture")
                        .setExtras(Bundle().apply {
                            putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, PlaybackRequestSequencer.next())
                        }).build()).build())
                controller.prepare()
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true)
            }
            await { onMain { controller.playbackState == Player.STATE_READY && activity.hasWindowFocus() } }
            surface = onMain { checkNotNull(findSurface(activity.window.decorView)) }
            val duration = onMain { controller.duration }
            report.put("durationMs", duration).put("originMs", origin)
            onMain { controller.pause(); controller.seekTo(origin) }
            await { frameMs()?.let { abs(it - origin) < 100 } == true }
            SystemClock.sleep(350)
            backend = onMain {
                (ExoSurfaceOutput::class.java.getDeclaredField("ownerReference").apply { isAccessible = true }
                    .get(null) as WeakReference<*>).get() as ExoPlayerBackend
            }
            val exo = onMain { backend.javaClass.getDeclaredField("player").apply { isAccessible = true }.get(backend) as Player }
            report.put("codecBefore", onMain { codecDetails(exo) })
            if (privateMedia) {
                val extractor = android.media.MediaExtractor()
                try {
                    extractor.setDataSource(context, android.net.Uri.parse(source), null)
                    val video = (0 until extractor.trackCount).first { extractor.getTrackFormat(it)
                        .getString(android.media.MediaFormat.KEY_MIME)?.startsWith("video/") == true }
                    extractor.selectTrack(video)
                    val sync = JSONArray()
                    for (target in origin..origin + 20_000 step 1000) {
                        extractor.seekTo(target * 1000, android.media.MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                        sync.put(JSONObject().put("targetMs", target).put("previousSyncMs", extractor.sampleTime / 1000))
                    }
                    report.put("syncPoints", sync)
                } finally { extractor.release() }
            }
            val slider = findSlider(checkNotNull(instrumentation.uiAutomation.rootInActiveWindow))
                ?: error("Fullscreen timeline has no accessibility range")
            val location = onMain { IntArray(2).also(activity.window.decorView::getLocationOnScreen) }
            val inset = 8 * context.resources.displayMetrics.density
            val left = slider.left - location[0] + inset
            val width = slider.width() - 2 * inset
            touchY = slider.exactCenterY() - location[1]
            fun send(target: Long) {
                touchX = left + width * target / duration
                if (native) exo.seekTo(target) else dispatch(MotionEvent.ACTION_MOVE, touchX, touchY, down)
            }
            onMain {
                if (native) backend.beginScrubbing() else {
                    touchX = left + width * origin / duration
                    dispatch(MotionEvent.ACTION_DOWN, touchX, touchY, down)
                    pointerDown = true
                }
            }
            val modes = JSONArray()
            for ((label, from, to) in listOf(Triple("forward", origin, origin + 20_000), Triple("reverse", origin + 20_000, origin))) {
                val start = SystemClock.elapsedRealtimeNanos()
                val requests = JSONArray()
                val samples = JSONArray()
                var activeInput = true
                val callback = object : Choreographer.FrameCallback {
                    override fun doFrame(frameTimeNanos: Long) {
                        if (!activeInput) return
                        val elapsed = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                        val target = from + ((to - from) * (elapsed / 2_000).coerceIn(0.0, 1.0)).toLong()
                        send(target)
                        requests.put(JSONObject().put("elapsedMs", elapsed).put("targetMs", target))
                        if (elapsed < 2_000) Choreographer.getInstance().postFrameCallback(this)
                    }
                }
                stopInput = { onMain { activeInput = false; Choreographer.getInstance().removeFrameCallback(callback) } }
                onMain { Choreographer.getInstance().postFrameCallback(callback) }
                while ((SystemClock.elapsedRealtimeNanos() - start) / 1_000_000 < 2_000) {
                    val frame = frameMs()
                    val elapsed = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                    if (frame != null) {
                        val target = from + ((to - from) * (elapsed / 2_000).coerceIn(0.0, 1.0)).toLong()
                        samples.put(JSONObject().put("elapsedMs", elapsed).put("targetMs", target)
                            .put("frameMs", frame).put("targetErrorMs", abs(target - frame)).put("producerHash", producerHash))
                    }
                    SystemClock.sleep(8)
                }
                stopInput.invoke(); stopInput = null
                onMain { send(to) }
                modes.put(JSONObject().put("mode", label).put("requests", requests).put("samples", samples))
            }
            report.put("modes", modes)
            report.put("codecWhileScrubbing", onMain { codecDetails(exo) })
            // Hold the finger still after each target. The source GOP is four seconds.
            // Waiting between jumps makes these exact preview requests, not >100x keyframe sweeps.
            val held = JSONArray()
            for (target in listOf(3_500L, 6_500L, 4_500L, 7_500L, 10_500L, 8_500L).map { origin + it }) {
                SystemClock.sleep(350)
                val start = SystemClock.elapsedRealtimeNanos()
                onMain { send(target) }
                var result: Long? = null
                await {
                    result = frameMs()
                    result?.let { abs(it - target) <= 100 } == true
                }
                held.put(JSONObject().put("targetMs", target).put("frameMs", result)
                    .put("visibleLatencyMs", (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0))
            }
            report.put("heldTargets", held)
            val commitStart = SystemClock.elapsedRealtimeNanos()
            onMain {
                if (native) { backend.endScrubbing(); exo.seekTo(origin + 8_500) }
                else { dispatch(MotionEvent.ACTION_UP, touchX, touchY, down); pointerDown = false }
            }
            await { frameMs()?.let { abs(it - origin - 8_500) <= 100 } == true && onMain {
                controller.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_NONE && !controller.playWhenReady
            } }
            report.put("commitMs", (SystemClock.elapsedRealtimeNanos() - commitStart) / 1_000_000.0)
            check(runBlocking { app.mediaRepositories.recent.findById(MediaId(id)) } == null)
            report.put("passed", true)
        } finally {
            stopInput?.invoke()
            onMain {
                if (pointerDown) dispatch(MotionEvent.ACTION_CANCEL, touchX, touchY, down)
                backend?.endScrubbing()
                vm.cancelSeekPreview()
                controller.stop(); controller.release()
            }
            File(context.cacheDir, "scrub-latency-${if (native) "native" else "adapter"}.json").writeText(report.toString(2))
            pixelThread.quitSafely()
            pixels.recycle()
        }
    }

    private fun frameMs(): Long? {
        if (!surface.holder.surface.isValid) return null
        val latch = CountDownLatch(1)
        var copied = false
        PixelCopy.request(surface, pixels, { copied = it == PixelCopy.SUCCESS; latch.countDown() }, Handler(pixelThread.looper))
        if (!latch.await(1, TimeUnit.SECONDS) || !copied) return null
        val colors = IntArray(pixels.width * pixels.height)
        pixels.getPixels(colors, 0, pixels.width, 0, 0, pixels.width, pixels.height)
        producerHash = colors.contentHashCode()
        if (privateMedia) return ExoFrameObservation.positionMs.takeIf { it >= 0 }
        fun white(x: Float): Boolean {
            val color = pixels.getPixel((x / 1280 * pixels.width).toInt(), pixels.height / 2)
            return Color.red(color) > 170 && Color.green(color) > 170 && Color.blue(color) > 170
        }
        if (!white(1184f)) return null
        var code = 0
        for (bit in 0..11) if (white(92f + 88 * bit)) code = code or (1 shl bit)
        return (code * 1000L / 24).takeIf { it < 90_000 }
    }

    private fun codecDetails(player: Player): JSONObject {
        val type = Class.forName("androidx.media3.exoplayer.ExoPlayer")
        val format = type.getMethod("getVideoFormat").invoke(player) as? androidx.media3.common.Format
        val count = type.getMethod("getRendererCount").invoke(player) as Int
        val rendererType = Class.forName("androidx.media3.exoplayer.Renderer")
        val renderer = (0 until count).map { type.getMethod("getRenderer", Int::class.javaPrimitiveType).invoke(player, it) }
            .firstOrNull { rendererType.getMethod("getTrackType").invoke(it) == androidx.media3.common.C.TRACK_TYPE_VIDEO }
        fun field(name: String): Any? {
            var type: Class<*>? = renderer?.javaClass
            while (type != null) {
                val current = type
                val found = runCatching { current.getDeclaredField(name).apply { isAccessible = true }.get(renderer) }.getOrNull()
                if (found != null) return found
                type = type.superclass
            }
            return null
        }
        return JSONObject().put("codec", field("codecInfo")?.toString())
            .put("operatingRate", field("codecOperatingRate"))
            .put("isFlushRequired", field("isFlushRequired"))
            .put("mime", format?.sampleMimeType).put("width", format?.width).put("height", format?.height)
            .put("frameRate", format?.frameRate).put("maxNumReorderSamples", format?.maxNumReorderSamples)
    }

    private fun findSlider(node: AccessibilityNodeInfo): android.graphics.Rect? {
        if (node.rangeInfo != null && node.isVisibleToUser) return android.graphics.Rect().also(node::getBoundsInScreen)
        for (index in 0 until node.childCount) node.getChild(index)?.let { findSlider(it)?.let { result -> return result } }
        return null
    }
    private fun findSurface(view: View): SurfaceView? = when (view) {
        is SurfaceView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findSurface(view.getChildAt(it)) }
        else -> null
    }
    private fun dispatch(action: Int, x: Float, y: Float, down: Long) {
        val event = MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, x, y, 0)
        try { activity.window.decorView.dispatchTouchEvent(event) } finally { event.recycle() }
    }
    private fun await(predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10_000
        while (SystemClock.elapsedRealtime() < deadline) { if (predicate()) return; SystemClock.sleep(8) }
        error("Scrub latency target timed out")
    }
    private fun <T> onMain(action: () -> T): T {
        var value: Result<T>? = null
        instrumentation.runOnMainSync { value = runCatching(action) }
        return checkNotNull(value).getOrThrow()
    }
}
