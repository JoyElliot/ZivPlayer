// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.graphics.SurfaceTexture
import android.media.MediaExtractor
import android.net.Uri
import android.os.SystemClock
import android.view.Surface
import android.view.TextureView
import android.view.ViewGroup
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.core.player.SeekPrecision
import io.github.joyelliot.zivplayer.core.player.runtime.*
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvBackend
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvSurfaceLease
import kotlinx.coroutines.*
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.Collections
import java.util.concurrent.atomic.AtomicLong

/** Opt-in local-file benchmark. Its private backend never writes the library/history. */
class SeekPresentationBenchmarkTest {
    @get:Rule val foreground = DeviceForegroundRule()

    @Test fun actualPresentationAfterSeek() = runBlocking {
        org.junit.Assume.assumeTrue(InstrumentationRegistry.getArguments().getString("seekBenchmark") == "true")
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val source = File(context.cacheDir, "seek-benchmark-source.json")
        org.junit.Assume.assumeTrue(source.exists())
        val uri = JSONObject(source.readText()).getString("source_uri")
        val report = JSONObject()
        val extractor = MediaExtractor()
        try {
            extractor.setDataSource(context, Uri.parse(uri), null)
            report.put("tracks", JSONArray((0 until extractor.trackCount).map { extractor.getTrackFormat(it).toString() }))
        } finally { extractor.release() }
        val backend = LibmpvBackend(context)
        val frames = Collections.synchronizedList(mutableListOf<Pair<Long, Long>>())
        val prepared = CompletableDeferred<Unit>()
        val completed = AtomicLong(0)
        val errors = Collections.synchronizedList(mutableListOf<String>())
        val collector = launch(Dispatchers.Default) {
            backend.events.collect { event ->
                when (event) {
                    is BackendEvent.Prepared -> prepared.complete(Unit)
                    is BackendEvent.SeekCompleted -> completed.set(event.seekGeneration.value)
                    is BackendEvent.Failure -> errors += event.error.toString()
                    else -> Unit
                }
            }
        }
        lateinit var texture: TextureView
        var surface: Surface? = null
        var lease: LibmpvSurfaceLease? = null
        val attached = CompletableDeferred<Unit>()
        instrumentation.runOnMainSync {
            val activity = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).first()
            texture = TextureView(activity)
            texture.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                override fun onSurfaceTextureAvailable(value: SurfaceTexture, width: Int, height: Int) {
                    value.setDefaultBufferSize(960, 540)
                    surface = Surface(value)
                    lease = backend.attachSurface(checkNotNull(surface))
                    backend.resizeSurface(checkNotNull(lease), 960, 540)
                    attached.complete(Unit)
                }
                override fun onSurfaceTextureSizeChanged(value: SurfaceTexture, width: Int, height: Int) = Unit
                override fun onSurfaceTextureDestroyed(value: SurfaceTexture): Boolean = true
                override fun onSurfaceTextureUpdated(value: SurfaceTexture) {
                    val at = SystemClock.elapsedRealtimeNanos()
                    val bitmap = texture.getBitmap(32, 18)
                    var hash = 1L
                    if (bitmap != null) {
                        for (y in 0 until bitmap.height) for (x in 0 until bitmap.width)
                            hash = hash * 31 + bitmap.getPixel(x, y)
                        bitmap.recycle()
                    }
                    frames += at to hash
                }
            }
            activity.addContentView(texture, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 540))
        }
        var serial = 0L
        suspend fun seek(target: Long, precision: SeekPrecision) {
            val next = ++serial
            backend.seekTo(BackendSeekRequest(LoadGeneration(1), SeekGeneration(next), Milliseconds(target), precision))
            withTimeout(10_000) { while (completed.get() < next) delay(2) }
        }
        fun trace(start: Long, end: Long): JSONObject {
            val data = synchronized(frames) { frames.filter { it.first in start..end } }
            val changes = data.filterIndexed { index, sample -> index == 0 || sample.second != data[index - 1].second }
            fun times(samples: List<Pair<Long, Long>>) = JSONArray(samples.map { (it.first - start) / 1_000_000.0 })
            return JSONObject().put("presentsMs", times(data)).put("pixelChangesMs", times(changes))
                .put("elapsedMs", (end - start) / 1_000_000.0)
        }
        try {
            withTimeout(5_000) { attached.await() }
            backend.load(BackendLoadRequest(LoadGeneration(1), QueueItem(QueueItemId("presentation-benchmark"),
                MediaItem(MediaId("presentation-benchmark"), MediaSource(uri))), Milliseconds(20_000)))
            withTimeout(15_000) { prepared.await() }
            backend.play()
            delay(2_000)
            report.put("diagnostics", backend.readDiagnostics().toString())
            val baselineStart = SystemClock.elapsedRealtimeNanos()
            delay(1_000)
            report.put("baseline", trace(baselineStart, SystemClock.elapsedRealtimeNanos()))
            val results = JSONArray()
            for ((target, twoSeeks) in listOf(60_000L to false, 120_000L to true, 180_000L to false,
                    245_500L to true, 310_000L to false)) {
                val start = SystemClock.elapsedRealtimeNanos()
                if (twoSeeks) seek(target, SeekPrecision.KEYFRAME)
                val exactStart = SystemClock.elapsedRealtimeNanos()
                seek(target, SeekPrecision.EXACT)
                val complete = SystemClock.elapsedRealtimeNanos()
                delay(1_200)
                results.put(trace(start, SystemClock.elapsedRealtimeNanos()).put("targetMs", target)
                    .put("twoSeeks", twoSeeks).put("exactStartMs", (exactStart - start) / 1_000_000.0)
                    .put("completionMs", (complete - start) / 1_000_000.0))
            }
            report.put("seeks", results).put("finalDiagnostics", backend.readDiagnostics().toString())
            report.put("errors", JSONArray(errors))
            check(errors.isEmpty()) { errors.toString() }
        } finally {
            File(context.cacheDir, "seek-presentation-benchmark.json").writeText(report.toString(2))
            backend.close()
            collector.cancelAndJoin()
            instrumentation.runOnMainSync {
                (texture.parent as? ViewGroup)?.removeView(texture)
                surface?.release()
            }
        }
    }
}
