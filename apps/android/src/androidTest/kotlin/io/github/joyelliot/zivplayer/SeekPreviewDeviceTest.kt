// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Color
import android.graphics.Bitmap
import android.graphics.Rect
import android.view.PixelCopy
import android.widget.ImageView
import android.os.Handler
import android.os.HandlerThread
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.TextureView
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.semantics.SemanticsActions
import androidx.lifecycle.ViewModelProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import io.github.joyelliot.zivplayer.platform.playback.SeekPreviewContract
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.concurrent.TimeUnit
import java.util.concurrent.CountDownLatch
import kotlin.math.abs

/** Measures rendered frame codes, not the controller's optimistic seek position. */
class SeekPreviewDeviceTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val baseline = InstrumentationRegistry.getArguments().getString("seekBaseline") == "true"
    private val pixelThread = HandlerThread("SeekWindowPixels").apply { start() }
    private val windowPixels = Bitmap.createBitmap(640, 360, Bitmap.Config.ARGB_8888)
    private lateinit var observedController: MediaController

    @Test fun renderedSeekAndLiveDrag() {
        val context = instrumentation.targetContext
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        observedController = controller
        val report = JSONObject().put("baseline", baseline)
        try {
            onMain {
                val sequence = PlaybackRequestSequencer.next()
                controller.setMediaItem(MediaItem.Builder().setMediaId("zivplayer-seek-preview-fixture")
                    .setUri(Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/seek-preview.mp4"))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Seek frame fixture").setExtras(Bundle().apply {
                        putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                    }).build()).build())
                controller.prepare()
            }
            await { onMain { controller.playbackState == Player.STATE_READY } }
            activity.waitUntil(5_000) { activity.onAllNodesWithText("Seek frame fixture").fetchSemanticsNodes().isNotEmpty() }
            activity.onNodeWithText("Seek frame fixture").performClick()
            onMain { controller.play() }
            await { onMain { controller.isPlaying && controller.currentPosition > 500 } }
            activity.onNodeWithContentDescription(context.getString(
                io.github.joyelliot.zivplayer.feature.player.R.string.player_rotate)).performClick()
            SystemClock.sleep(1_000)
            onMain { controller.pause(); controller.seekTo(20_000) }
            SystemClock.sleep(500)
            instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
                File(context.cacheDir, "seek-screen.png").outputStream().use {
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
                }
                bitmap.recycle()
            }
            onMain {
                fun find(view: View): TextureView? = when (view) {
                    is TextureView -> view
                    is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { find(view.getChildAt(it)) }
                    else -> null
                }
                find(activity.activity.window.decorView)?.let { texture ->
                    report.put("textureSize", "${texture.width}x${texture.height}")
                    texture.bitmap?.let { bitmap ->
                        File(context.cacheDir, "seek-calibration.png").outputStream().use {
                            bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
                        }
                        bitmap.recycle()
                    }
                }
            }
            await { renderedMs()?.let { abs(it - 20_000) < 100 } == true }
            val latencies = JSONArray()
            for (target in listOf(25_000L, 20_000L, 31_500L, 18_500L, 43_500L)) {
                val start = SystemClock.elapsedRealtime()
                onMain { controller.seekTo(target) }
                await { renderedMs()?.let { abs(it - target) < 100 } == true }
                latencies.put(JSONObject().put("targetMs", target).put("visibleFrameMs", renderedMs())
                    .put("latencyMs", SystemClock.elapsedRealtime() - start))
            }
            report.put("exactSeeks", latencies)
            onMain { controller.seekTo(20_000) }
            await { renderedMs()?.let { abs(it - 20_000) < 100 } == true }
            // Raw events let native rendering continue between MOVE events while the finger stays down.
            val bounds = activity.onNodeWithContentDescription(context.getString(
                io.github.joyelliot.zivplayer.feature.player.R.string.player_video_area)).fetchSemanticsNode().boundsInRoot
            val startX = bounds.left + bounds.width * .38f
            val endX = bounds.left + bounds.width * .68f
            val y = bounds.top + bounds.height * .3f
            val downTime = SystemClock.uptimeMillis()
            touch(MotionEvent.ACTION_DOWN, startX, y, downTime)
            val frames = mutableSetOf<Long>()
            val previewDiagnostics = JSONArray()
            for (step in 1..20) {
                touch(MotionEvent.ACTION_MOVE, startX + (endX - startX) * step / 20, y, downTime)
                SystemClock.sleep(70)
                renderedMs()?.let(frames::add)
                val state = onMain { ViewModelProvider(activity.activity)[PlaybackControllerViewModel::class.java].seekPreviewFrame.value }
                previewDiagnostics.put(JSONObject().put("previewMs", state?.positionMs).put("visible", previewVisible()))
            }
            report.put("previewDiagnostics", previewDiagnostics)
            report.put("framesWhileFingerDown", JSONArray(frames.toList()))
            touch(MotionEvent.ACTION_UP, endX, y, downTime)
            await { !previewVisible() && renderedMs()?.let { abs(it - 47_000) < 100 } == true }
            report.put("afterReleaseMs", renderedMs())
            if (!baseline) assertTrue("No live rendered preview: $report", frames.count { abs(it - 20_000) > 100 } >= 3)
            assertTrue(onMain { !controller.playWhenReady })
            if (!baseline) {
                val viewModel = onMain { ViewModelProvider(activity.activity)[PlaybackControllerViewModel::class.java] }
                onMain { controller.seekTo(20_000) }
                await { renderedMs()?.let { abs(it - 20_000) < 100 } == true }
                val interactive = JSONArray()
                var from = 20_000L
                for (delta in listOf(5_000L, -5_000L, 5_000L)) {
                    val target = from + delta
                    val start = SystemClock.elapsedRealtime()
                    onMain { viewModel.seekBy(delta) }
                    var firstFrame: Long? = null
                    await {
                        val visible = renderedMs()
                        if (firstFrame == null && visible != null && abs(visible - from) > 100)
                            firstFrame = SystemClock.elapsedRealtime() - start
                        visible != null && abs(visible - target) < 100
                    }
                    interactive.put(JSONObject().put("fromMs", from).put("targetMs", target)
                        .put("firstChangedFrameLatencyMs", firstFrame)
                        .put("exactFrameLatencyMs", SystemClock.elapsedRealtime() - start))
                    from = target
                }
                report.put("interactiveFiveSecondSeeks", interactive)
                onMain { viewModel.seekBy(5_000); viewModel.seekBy(5_000) }
                await { renderedMs()?.let { abs(it - 35_000) < 100 } == true }
                report.put("rapidTwoForwardSeeksMs", renderedMs())

                val timeline = activity.onNode(SemanticsMatcher.keyIsDefined(SemanticsActions.SetProgress))
                    .fetchSemanticsNode().boundsInRoot
                val sliderDown = SystemClock.uptimeMillis()
                val sliderFrames = mutableSetOf<Long>()
                touch(MotionEvent.ACTION_DOWN, timeline.left + timeline.width * .2f, timeline.center.y, sliderDown)
                for (step in 1..20) {
                    touch(MotionEvent.ACTION_MOVE, timeline.left + timeline.width * (.2f + .5f * step / 20), timeline.center.y, sliderDown)
                    SystemClock.sleep(70)
                    renderedMs()?.let(sliderFrames::add)
                }
                report.put("sliderFramesWhileFingerDown", JSONArray(sliderFrames.toList()))
                assertTrue("Slider did not render live previews: $report", sliderFrames.size >= 3)
                val inset = 8 * context.resources.displayMetrics.density
                val sliderTarget = ((timeline.width * .7f - inset) / (timeline.width - 2 * inset) * 90_000).toLong()
                touch(MotionEvent.ACTION_UP, timeline.left + timeline.width * .7f, timeline.center.y, sliderDown)
                await { !previewVisible() && renderedMs()?.let { abs(it - sliderTarget) < 125 } == true }
                report.put("sliderTargetMs", sliderTarget).put("sliderCommittedFrameMs", renderedMs())

                onMain { controller.seekTo(20_000) }
                await { renderedMs()?.let { abs(it - 20_000) < 100 } == true }
                val cancelDown = SystemClock.uptimeMillis()
                touch(MotionEvent.ACTION_DOWN, startX, y, cancelDown)
                for (step in 1..8) {
                    touch(MotionEvent.ACTION_MOVE, startX + (endX - startX) * step / 8, y, cancelDown)
                    SystemClock.sleep(25)
                    activity.waitForIdle()
                }
                await { renderedMs()?.let { it > 30_000 } == true }
                touch(MotionEvent.ACTION_CANCEL, endX, y, cancelDown)
                await { renderedMs()?.let { abs(it - 20_000) < 100 } == true }
                report.put("cancelRestoredFrameMs", renderedMs())

                val sequence = onMain { controller.currentMediaItem!!.mediaMetadata.extras!!.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA) }
                fun previewCommand(token: Long, phase: Int, position: Long, requestSequence: Long = sequence) {
                    val result = onMain { controller.sendCustomCommand(SessionCommand(SeekPreviewContract.ACTION, Bundle.EMPTY), Bundle().apply {
                        putLong(SeekPreviewContract.TOKEN, token); putInt(SeekPreviewContract.PHASE, phase)
                        putLong(SeekPreviewContract.POSITION, position)
                        putString(SeekPreviewContract.MEDIA_ID, "zivplayer-seek-preview-fixture")
                        putLong(SeekPreviewContract.SEQUENCE, requestSequence); putInt(SeekPreviewContract.INDEX, 0)
                    }) }.get(5, TimeUnit.SECONDS)
                    assertTrue(result.resultCode == SessionResult.RESULT_SUCCESS)
                }
                previewCommand(100, SeekPreviewContract.PREVIEW, 32_000)
                await { renderedMs()?.let { it in 28_000..32_000 } == true }
                previewCommand(100, SeekPreviewContract.COMMIT, 33_500)
                await { renderedMs()?.let { abs(it - 33_500) < 100 } == true }
                previewCommand(100, SeekPreviewContract.PREVIEW, 50_000)
                previewCommand(101, SeekPreviewContract.PREVIEW, 60_000, sequence - 1)
                SystemClock.sleep(400)
                assertTrue("Stale token/identity moved the video", renderedMs()?.let { abs(it - 33_500) < 100 } == true)
                report.put("staleCommandsIgnored", true)

                onMain { controller.play() }
                await { onMain { controller.isPlaying } }
                onMain { viewModel.previewSeekTo(44_000) }
                await { renderedMs()?.let { it >= 44_000 } == true }
                assertTrue(onMain { controller.playWhenReady && !controller.isPlaying &&
                    controller.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING })
                val heldPosition = onMain { controller.currentPosition }
                SystemClock.sleep(250)
                assertTrue("Main playback continued behind the preview", onMain { abs(controller.currentPosition - heldPosition) < 80 })
                report.put("mainPlaybackSuppressedDuringDrag", true)
                onMain { viewModel.seekTo(45_500) }
                await { !previewVisible() && renderedMs()?.let { it in 45_500..47_000 } == true && onMain { controller.isPlaying } }
                report.put("playingIntentPreserved", onMain { controller.playWhenReady })

                val cancelOrigin = onMain { controller.currentPosition }
                onMain { viewModel.previewSeekTo(55_000) }
                await { previewVisible() && onMain { !controller.isPlaying && controller.playWhenReady } }
                onMain { viewModel.cancelSeekPreview() }
                await { !previewVisible() && onMain { controller.isPlaying && abs(controller.currentPosition - cancelOrigin) < 1_200 } }
                report.put("playingCancelRestoresIntent", true)
                onMain { viewModel.previewSeekTo(60_000) }
                await { previewVisible() && onMain { !controller.isPlaying } }
                onMain { controller.pause() }
                await { !previewVisible() && onMain { !controller.playWhenReady } }
                SystemClock.sleep(300)
                assertTrue("External pause was overridden", onMain { !controller.isPlaying && !controller.playWhenReady })
                report.put("externalPauseOverridesPreview", true)

                onMain { controller.pause(); controller.seekTo(20_000) }
                await { renderedMs()?.let { abs(it - 20_000) < 100 } == true }
                previewCommand(102, SeekPreviewContract.PREVIEW, 40_000)
                await { renderedMs()?.let { it in 36_000..40_000 } == true }
                onMain { controller.stop() }
                await { onMain { controller.playbackState == Player.STATE_IDLE } }
                previewCommand(102, SeekPreviewContract.COMMIT, 60_000)
                onMain { controller.play() }
                await { onMain { controller.isPlaying && controller.currentPosition > 200 } }
                onMain { controller.pause() }
                await { onMain { !controller.playWhenReady } }
                onMain { viewModel.previewSeekTo(42_000) }
                await { renderedMs()?.let { it in 38_000..42_000 } == true }
                onMain { viewModel.seekTo(43_500) }
                await { !previewVisible() && renderedMs()?.let { abs(it - 43_500) < 100 } == true }
                report.put("stopReplayAcceptsNewPreview", true)

                val temporaryController = onMain { MediaController.Builder(context, SessionToken(context,
                    ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
                try {
                    onMain { temporaryController.sendCustomCommand(SessionCommand(SeekPreviewContract.ACTION, Bundle.EMPTY), Bundle().apply {
                        putLong(SeekPreviewContract.TOKEN, 1); putInt(SeekPreviewContract.PHASE, SeekPreviewContract.PREVIEW)
                        putLong(SeekPreviewContract.POSITION, 52_000)
                        putString(SeekPreviewContract.MEDIA_ID, "zivplayer-seek-preview-fixture")
                        putLong(SeekPreviewContract.SEQUENCE, sequence); putInt(SeekPreviewContract.INDEX, 0)
                    }) }.get(5, TimeUnit.SECONDS)
                    await { renderedMs()?.let { it in 48_000..52_000 } == true }
                } finally { onMain { temporaryController.release() } }
                await { renderedMs()?.let { abs(it - 43_500) < 100 } == true }
                onMain { viewModel.previewSeekTo(54_000); viewModel.seekTo(54_500) }
                await { !previewVisible() && renderedMs()?.let { abs(it - 54_500) < 100 } == true }
                report.put("disconnectRestoresAndReleasesPreview", true)
                onMain { viewModel.previewSeekTo(64_000) }
                await { renderedMs()?.let { it in 60_000..64_000 } == true }
                try {
                    onMain { activity.activity.onWindowFocusChanged(false) }
                    await { renderedMs()?.let { abs(it - 54_500) < 100 } == true }
                } finally { onMain { activity.activity.onWindowFocusChanged(true) } }
                report.put("focusLossCancelsPreview", true)

                onMain { viewModel.previewSeekTo(36_000, allowLocalDecoder = false) }
                await { renderedMs()?.let { it in 32_000..36_000 } == true }
                onMain { viewModel.previewSeekTo(40_000, allowLocalDecoder = false) }
                await { renderedMs()?.let { it in 36_000..40_000 } == true }
                onMain { controller.seekTo(22_500) }
                await { renderedMs()?.let { abs(it - 22_500) < 100 } == true }
                onMain { viewModel.previewSeekTo(32_000, allowLocalDecoder = false) }
                await { renderedMs()?.let { it in 28_000..32_000 } == true }
                onMain { viewModel.cancelSeekPreview() }
                await { renderedMs()?.let { abs(it - 22_500) < 100 } == true }
                report.put("externalSeekRetiresNativePreview", true)

                onMain { viewModel.previewSeekTo(70_000) }
                await { previewVisible() }
                onMain {
                    viewModel.seekTo(71_500)
                    fun texture(view: View): View? = when (view) {
                        is TextureView, is SurfaceView -> view
                        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { texture(view.getChildAt(it)) }
                        else -> null
                    }
                    val video = checkNotNull(texture(activity.activity.window.decorView))
                    val parent = video.parent as ViewGroup
                    parent.removeView(video)
                    parent.addView(video, 0)
                }
                await { !previewVisible() && renderedMs()?.let { abs(it - 71_500) < 100 } == true }
                report.put("surfaceRecreatedDuringCommit", true)
            }
        } finally {
            File(context.cacheDir, "seek-${if (baseline) "before" else "after"}.json").writeText(report.toString(2))
            onMain { controller.stop(); controller.release() }
            pixelThread.quitSafely()
            windowPixels.recycle()
        }
    }

    private fun touch(action: Int, x: Float, y: Float, down: Long) = onMain {
        val event = MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, x, y, 0)
        try { activity.activity.window.decorView.dispatchTouchEvent(event) } finally { event.recycle() }
    }
    // Direct Surface previews have no ImageView. Observe the lease/suppression
    // state for lifecycle assertions; frame assertions still sample actual pixels.
    private fun previewVisible(): Boolean = bitmapPreviewVisible() || onMain {
        observedController.playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING
    }
    private fun bitmapPreviewVisible(): Boolean = onMain {
        activity.activity.window.decorView.findViewWithTag<ImageView>("seek-preview-frame")?.let {
            it.isShown && it.drawable != null
        } == true
    }

    private fun renderedMs(): Long? {
        // Raw MotionEvents do not drive Compose Test's controlled frame clock.
        // Flush the pending overlay composition before sampling the window's pixels.
        activity.waitForIdle()
        if (bitmapPreviewVisible()) return compositedFrameMs()
        val surface = onMain {
            fun find(view: View): SurfaceView? = when (view) {
                is SurfaceView -> view
                is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { find(view.getChildAt(it)) }
                else -> null
            }
            find(activity.activity.window.decorView)
        }
        if (surface != null) {
            val complete = CountDownLatch(1)
            var copied = false
            if (!surface.holder.surface.isValid) return null
            PixelCopy.request(surface, windowPixels, { result -> copied = result == PixelCopy.SUCCESS; complete.countDown() }, Handler(pixelThread.looper))
            if (!complete.await(1, TimeUnit.SECONDS) || !copied) return null
            return decodeFrame(windowPixels)
        }
        return onMain {
        fun texture(view: View): TextureView? = when (view) {
            is TextureView -> view
            is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { texture(view.getChildAt(it)) }
            else -> null
        }
        val view = texture(activity.activity.window.decorView) ?: return@onMain null
        val bitmap = view.getBitmap(640, 360) ?: return@onMain null
        try {
            // FIT letterboxing is horizontal on the landscape test device.
            val contentWidth = minOf(640f, 360f * view.height / view.width * (16f / 9f) * (640f / 360f))
            val offset = (640 - contentWidth) / 2
            fun white(sourceX: Float): Boolean {
                val pixel = bitmap.getPixel((offset + sourceX / 1280 * contentWidth).toInt().coerceIn(0, 639), 180)
                return Color.red(pixel) > 170 && Color.green(pixel) > 170 && Color.blue(pixel) > 170
            }
            if (!white(1184f)) return@onMain null
            var frame = 0
            for (bit in 0..11) if (white(92f + 88 * bit)) frame = frame or (1 shl bit)
            (frame * 1000L / 24).takeIf { it < 90_000 }
        } finally { bitmap.recycle() }
        }
    }

    private fun decodeFrame(bitmap: Bitmap): Long? {
        fun white(x: Float): Boolean {
            val pixel = bitmap.getPixel((x / 1280 * bitmap.width).toInt(), bitmap.height / 2)
            return Color.red(pixel) > 170 && Color.green(pixel) > 170 && Color.blue(pixel) > 170
        }
        if (!white(1184f)) return null
        var code = 0
        for (bit in 0..11) if (white(92f + 88 * bit)) code = code or (1 shl bit)
        return (code * 1000L / 24).takeIf { it < 90_000 }
    }

    private fun compositedFrameMs(): Long? {
        val rect = onMain {
            val view = activity.activity.window.decorView.findViewWithTag<ImageView>("seek-preview-frame") ?: return@onMain null
            val location = IntArray(2); view.getLocationInWindow(location)
            Rect(location[0], location[1], location[0] + view.width, location[1] + view.height)
        } ?: return null
        val complete = CountDownLatch(1)
        var copied = false
        PixelCopy.request(activity.activity.window, rect, windowPixels, { result -> copied = result == PixelCopy.SUCCESS; complete.countDown() }, Handler(pixelThread.looper))
        if (!complete.await(1, TimeUnit.SECONDS) || !copied) return null
        // This row is within the barcode but below the centered drag-time HUD.
        fun white(x: Float): Boolean {
            val pixel = windowPixels.getPixel((x / 1280 * 640).toInt(), 212)
            return Color.red(pixel) > 170 && Color.green(pixel) > 170 && Color.blue(pixel) > 170
        }
        if (!white(1184f)) return null
        var code = 0
        for (bit in 0..11) if (white(92f + 88 * bit)) code = code or (1 shl bit)
        return (code * 1000L / 24).takeIf { it < 90_000 }
    }
    private fun await(predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10_000
        while (SystemClock.elapsedRealtime() < deadline) {
            activity.waitForIdle()
            if (predicate()) return
            SystemClock.sleep(20)
        }
        error("Timed out; renderedMs=${renderedMs()}")
    }
    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
