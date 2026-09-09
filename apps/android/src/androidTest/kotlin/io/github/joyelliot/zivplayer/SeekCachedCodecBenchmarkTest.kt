// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.net.Uri
import android.os.SystemClock
import android.view.ViewGroup
import android.widget.ImageView
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Rule
import org.junit.Test
import java.io.File

class SeekCachedCodecBenchmarkTest {
    @get:Rule val foreground = DeviceForegroundRule()
    @Test fun cachedGpuFrames() {
        org.junit.Assume.assumeTrue(InstrumentationRegistry.getArguments().getString("seekBenchmark") == "true")
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        org.junit.Assume.assumeTrue(File(context.cacheDir, "seek-benchmark-source.json").exists())
        val uri = JSONObject(File(context.cacheDir, "seek-benchmark-source.json").readText()).getString("source_uri")
        lateinit var image: ImageView
        instrumentation.runOnMainSync {
            val activity = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).first()
            image = ImageView(activity)
            activity.addContentView(image, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 540))
        }
        val report = JSONObject()
        try {
            ScrubFrameExtractor(context, Uri.parse(uri)).use { decoder ->
                report.put("decoder", decoder.decoderName)
                val modes = JSONArray()
                for ((name, targets) in listOf("forward" to (0 until 30).map { 500_000_000L + it * 200_000 },
                        "reverse-cached" to (0 until 30).map { 505_800_000L - it * 200_000 },
                        "reverse-uncached" to (0 until 30).map { 500_000_000L - it * 200_000 })) {
                    val samples = JSONArray()
                    for (target in targets) {
                        val start = SystemClock.elapsedRealtimeNanos()
                        val frame = checkNotNull(decoder.frameAt(target))
                        val end = SystemClock.elapsedRealtimeNanos()
                        instrumentation.runOnMainSync { image.setImageBitmap(frame.bitmap) }
                        val elapsed = (end - start) / 1_000_000.0
                        samples.put(JSONObject().put("targetUs", target).put("frameUs", frame.positionUs).put("decodeMs", elapsed))
                        if (elapsed < 50) SystemClock.sleep((50 - elapsed).toLong())
                    }
                    modes.put(JSONObject().put("mode", name).put("samples", samples))
                }
                report.put("modes", modes)
            }
        } finally {
            File(context.cacheDir, "seek-cached-codec-benchmark.json").writeText(report.toString(2))
            instrumentation.runOnMainSync { image.setImageDrawable(null); (image.parent as? ViewGroup)?.removeView(image) }
        }
    }
}
