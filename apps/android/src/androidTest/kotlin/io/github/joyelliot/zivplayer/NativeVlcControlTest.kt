// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.os.*
import android.view.*
import android.widget.FrameLayout
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.lang.reflect.Proxy
import java.security.MessageDigest
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** Opt-in control: same packaged LibVLC, independent player and surfaces, no app playback commands. */
class NativeVlcControlTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    @Test fun nativeSubtitleSeekControl() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.containsKey("nativeVlcMode"))
        val mode = args.getString("nativeVlcMode")!!.also { require(it in setOf("linear", "native", "bridge")) }
        val textIndex = args.getString("nativeVlcTextIndex", "0").toInt().also { require(it in 0..1) }
        val position = args.getString("nativeVlcPositionMs", "5000").toLong().also { require(it in 2000..6000) }
        val label = args.getString("nativeVlcLabel")!!.also { require(it.matches(Regex("[a-z0-9-]{1,80}"))) }
        val expectedHash = args.getString("nativeVlcFixtureSha256")!!
        val fixture = File(context.filesDir, "power-fixtures/clarity-subtitles.mkv")
        val hash = MessageDigest.getInstance("SHA-256").digest(fixture.readBytes()).joinToString("") { "%02x".format(it) }
        check(hash == expectedHash)
        val output = File(context.filesDir, "native-vlc-control-results/$label")
        check(!output.exists() && output.mkdirs())
        val events = JSONArray()
        val report = JSONObject().put("label", label).put("mode", mode).put("textIndex", textIndex)
            .put("requestedPositionMs", position).put("fixtureSha256", hash)
            .put("path", "Independent LibVLC/MediaPlayer, PFD and two SurfaceViews; no backend/core/MediaController media request")
            .put("events", events)
        var activity: MainActivity? = null
        var player: Any? = null
        var lib: Any? = null
        var media: Any? = null
        var bridge: Any? = null
        var descriptor: ParcelFileDescriptor? = null
        try {
            DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
            await(15000) { activity = resumedActivity(); activity != null }
            val active = checkNotNull(activity)
            onMain {
                active.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
                active.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
            lateinit var video: SurfaceView
            lateinit var subtitles: SurfaceView
            lateinit var root: FrameLayout
            onMain {
                root = FrameLayout(active)
                video = SurfaceView(active)
                subtitles = SurfaceView(active).apply {
                    setZOrderMediaOverlay(true)
                    holder.setFormat(PixelFormat.TRANSLUCENT)
                }
                for (view in listOf(video, subtitles)) root.addView(view,
                    FrameLayout.LayoutParams(1920, 1080, Gravity.CENTER))
                active.setContentView(root)
                WindowInsetsControllerCompat(active.window, root).hide(WindowInsetsCompat.Type.systemBars())
            }
            await(15000) { onMain { active.hasWindowFocus() && root.width == 2400 && root.height == 1080 &&
                video.holder.surface.isValid && subtitles.holder.surface.isValid } }
            val libType = Class.forName("org.videolan.libvlc.LibVLC")
            val interfaceType = Class.forName("org.videolan.libvlc.interfaces.ILibVLC")
            val mediaType = Class.forName("org.videolan.libvlc.Media")
            val playerType = Class.forName("org.videolan.libvlc.MediaPlayer")
            val listenerType = Class.forName("org.videolan.libvlc.MediaPlayer\$EventListener")
            val layoutType = Class.forName("org.videolan.libvlc.interfaces.IVLCVout\$OnNewVideoLayoutListener")
            var nativeError = false
            var bridgeCompleted: Long? = null
            onMain {
                lib = libType.getConstructor(android.content.Context::class.java, List::class.java)
                    .newInstance(context, arrayListOf("--no-video-title-show"))
                descriptor = ParcelFileDescriptor.open(fixture, ParcelFileDescriptor.MODE_READ_ONLY)
                media = mediaType.getConstructor(interfaceType, java.io.FileDescriptor::class.java)
                    .newInstance(lib, checkNotNull(descriptor).fileDescriptor)
                player = playerType.getConstructor(interfaceType).newInstance(lib)
                val p = checkNotNull(player)
                call(p, "setMedia", checkNotNull(media))
                call(p, "setVolume", 0)
                call(p, "setAspectRatio", "16:9")
                val listener = Proxy.newProxyInstance(listenerType.classLoader, arrayOf(listenerType)) { _, method, values ->
                    if (method.name == "onEvent") {
                        val event = checkNotNull(values?.get(0))
                        val type = event.javaClass.getField("type").getInt(event)
                        events.put(JSONObject().put("type", type).put("elapsedMs", SystemClock.elapsedRealtime())
                            .put("nativeTimeMs", call(p, "getTime")))
                        if (type == 266) nativeError = true
                    }
                    null
                }
                call(p, "setEventListener", listener)
                val vout = call(p, "getVLCVout")!!
                call(vout, "setVideoView", video)
                call(vout, "setSubtitlesView", subtitles)
                val layout = Proxy.newProxyInstance(layoutType.classLoader, arrayOf(layoutType)) { _, method, values ->
                    if (method.name == "onNewVideoLayout") report.put("nativeLayout", JSONArray(values!!.drop(1)))
                    null
                }
                call(vout, "attachViews", layout)
                call(vout, "setWindowSize", 1920, 1080)
                if (mode == "bridge") {
                    val bridgeType = Class.forName("io.github.joyelliot.zivplayer.platform.libvlc.VlcSeekBridge")
                    val completed: (Long, Long) -> Unit = { token, actual ->
                        bridgeCompleted = actual
                        report.put("bridgeCompletion", JSONObject().put("token", token).put("positionMs", actual))
                    }
                    bridge = bridgeType.constructors.single().newInstance(call(p, "getInstance"), completed)
                }
                call(p, "play")
            }
            val p = checkNotNull(player)
            fun time() = onMain { (call(p, "getTime") as Number).toLong() }
            await(15000) { onMain {
                check(!nativeError)
                (call(p, "getTracks", 2) as? Array<*>)?.size == 2 && (call(p, "getTime") as Long) >= 300
            } }
            onMain {
                val tracks = call(p, "getTracks", 2) as Array<*>
                val selected = checkNotNull(tracks[textIndex])
                val id = selected.javaClass.getField("id").get(selected) as String
                report.put("requestedTextId", id)
                check(call(p, "selectTrack", id) == true)
            }
            if (mode == "linear") {
                await(15000) { time() >= position }
                onMain { call(p, "pause") }
            } else {
                onMain { call(p, "pause") }
                await(5000) { onMain { call(p, "isPlaying") == false } }
                SystemClock.sleep(500)
                report.put("beforeSeekTimeMs", time())
                onMain {
                    if (mode == "native") report.put("setTimeResult", call(p, "setTime", position, false))
                    else call(checkNotNull(bridge), "seek", 1L, position, false, true)
                }
                // Capture actual outcome, including a seek that returns a different time.
                SystemClock.sleep(2500)
            }
            await(5000) { onMain { call(p, "isPlaying") == false } }
            SystemClock.sleep(800)
            onMain {
                val selected = call(p, "getSelectedTrack", 2)
                report.put("selectedTextId", selected?.javaClass?.getField("id")?.get(selected))
                    .put("nativeTimeMs", call(p, "getTime")).put("nativeIsPlaying", call(p, "isPlaying"))
                    .put("bridgeCompletedPositionMs", bridgeCompleted)
                check(!nativeError)
            }
            capture(output, "subtitle-on")
            report.put("subtitleSurfaceHasBuffer", copySubtitle(subtitles, File(output, "subtitle-raw.png")))
            onMain { call(p, "unselectTrackType", 2) }
            SystemClock.sleep(700)
            capture(output, "subtitle-off")
            report.put("completed", true)
        } catch (error: Throwable) {
            report.put("failure", error.toString())
            runCatching { capture(output, "failure-screen") }
            throw error
        } finally {
            onMain {
                bridge?.let { runCatching { call(it, "close") } }
                player?.let { p ->
                    runCatching { call(p, "setEventListener", null) }
                    runCatching { call(p, "stop") }
                    runCatching { call(call(p, "getVLCVout")!!, "detachViews") }
                    runCatching { call(p, "release") }
                }
                media?.let { runCatching { call(it, "release") } }
                descriptor?.close()
                lib?.let { runCatching { call(it, "release") } }
                activity?.finish()
            }
            File(output, "report.json").writeText(report.toString(2))
        }
    }

    private fun capture(output: File, name: String) {
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        try { File(output, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
    }
    private fun copySubtitle(view: SurfaceView, file: File): Boolean {
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        try {
            val result = CompletableFuture<Int>()
            PixelCopy.request(view.holder.surface, bitmap, { result.complete(it) }, Handler(Looper.getMainLooper()))
            val code = result.get(5, TimeUnit.SECONDS)
            if (code == PixelCopy.ERROR_SOURCE_NO_DATA) return false
            check(code == PixelCopy.SUCCESS) { "Subtitle readback failed: $code" }
            file.outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
            return true
        } finally { bitmap.recycle() }
    }
    private fun call(target: Any, name: String, vararg args: Any?): Any? {
        val method = target.javaClass.methods.single { method -> method.name == name && method.parameterCount == args.size &&
            method.parameterTypes.zip(args).all { (type, arg) -> arg == null || type.isInstance(arg) ||
                (type == java.lang.Integer.TYPE && arg is Int) || (type == java.lang.Long.TYPE && arg is Long) ||
                (type == java.lang.Boolean.TYPE && arg is Boolean) } }
        return method.invoke(target, *args)
    }
    private fun resumedActivity(): MainActivity? = onMain {
        ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).filterIsInstance<MainActivity>().firstOrNull()
    }
    private fun await(timeout: Long, condition: () -> Boolean) {
        val end = SystemClock.elapsedRealtime() + timeout
        while (SystemClock.elapsedRealtime() < end) { if (condition()) return; SystemClock.sleep(50) }
        error("Native control readiness timeout")
    }
    private fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(block) }
        return checkNotNull(result).getOrThrow()
    }
}
