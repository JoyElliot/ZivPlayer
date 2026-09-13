// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.content.Context
import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.media.AudioManager
import android.media.MediaCodecList
import android.os.*
import android.provider.Settings
import android.view.*
import androidx.core.content.FileProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.*
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.platform.playback.*
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoSurfaceOutput
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.lang.ref.WeakReference
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** One cold process per leg, with the real Activity/MediaSession/service and local 1 Hz telemetry. */
class PowerPlaybackBenchmarkTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    @Test fun steadyPlaybackBatteryTrace() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("powerBenchmark") == "true")
        val engine = checkNotNull(args.getString("engine")).also { require(it in setOf("exo", "libmpv")) }
        val codec = checkNotNull(args.getString("fixture")).also { require(it in setOf("avc", "hevc", "hevc10", "vp9", "av1")) }
        val mode = args.getString("decoderMode", "automatic").also { require(it in setOf("automatic", "software")) }
        val surfaceProtocol = args.getString("surfaceProtocol", "matched-1080p").also { require(it in setOf("matched-1080p", "current-app")) }
        val validationOnly = args.getString("validationOnly") == "true"
        val allowExternalPower = args.getString("allowExternalPower") == "true"
        require(!allowExternalPower || validationOnly) { "External power is allowed only for functional validation." }
        val minimumBatteryPercent = args.getString("minimumBatteryPercent", "25").toInt()
            .also { require(it in 5..25) { "The per-sample battery guard must be between 5 and 25 percent." } }
        val label = checkNotNull(args.getString("runLabel")).also { require(it.matches(Regex("[a-zA-Z0-9_-]{1,100}"))) }
        val warmupSeconds = args.getString("warmupSeconds", "60").toInt().also { require(it in 5..120) }
        val measureSeconds = args.getString("measureSeconds", "240").toInt().also { require(it in 10..240) }
        val fixture = File(context.filesDir, "power-fixtures/$codec-1080p24.mkv")
        val resultDir = File(context.filesDir, "power-results").apply { mkdirs() }
        val resultFile = File(resultDir, "$label.json")
        val traceFile = File(resultDir, "$label.jsonl")
        check(!resultFile.exists() && !traceFile.exists()) { "Never overwrite an earlier measurement." }
        val report = JSONObject().put("schemaVersion", 1).put("runLabel", label)
            .put("requestedEngine", engine).put("requestedDecoderMode", mode).put("fixture", fixture.name)
            .put("surfaceProtocol", surfaceProtocol).put("validationOnly", validationOnly)
            .put("measurementCohort", args.getString("measurementCohort", "unassigned"))
            .put("controlProtocolVersion", 2)
            .put("fixtureStorage", "private-files").put("resultsStorage", "private-files")
            .put("allowExternalPower", allowExternalPower)
            .put("minimumBatteryPercent", minimumBatteryPercent)
            .put("model", Build.MODEL).put("sdk", Build.VERSION.SDK_INT)
            .put("warmupSeconds", warmupSeconds).put("measurementSeconds", measureSeconds)
            .put("sourceFramesPerSecond", 24).put("powerEvidence", "Raw BatteryManager current and sticky battery voltage; no modeled app-power result")
            .put("counterUnits", "Current API microamps; voltage API millivolts; charge/energy counters vendor raw, unsupported sentinels retained")
        val app = context.applicationContext as ZivPlayerApplication
        val id = "power-benchmark-${UUID.randomUUID()}"
        var activity: MainActivity? = null
        var controller: MediaController? = null
        var observedSurface: SurfaceView? = null
        var surfaceObserver: SurfaceHolder.Callback? = null
        val surfaceEvents = JSONArray()
        val pixelThread = HandlerThread("PowerFrameProof").apply { start() }
        val pixels = Bitmap.createBitmap(96, 54, Bitmap.Config.ARGB_8888)
        var primaryFailure: Throwable? = null
        try {
            report.put("stage", "fixture_hash")
            check(fixture.isFile) { "The staged power fixture is missing." }
            check(runBlocking { app.recentMediaRepository.findById(MediaId(id)) } == null)
            // Hashing also gives every leg an identical full-file page-cache warmup before playback.
            val hash = MessageDigest.getInstance("SHA-256")
            fixture.inputStream().buffered().use { input ->
                val buffer = ByteArray(1024 * 1024)
                while (true) { val count = input.read(buffer); if (count < 0) break; hash.update(buffer, 0, count) }
            }
            report.put("fixtureSha256", hash.digest().joinToString("") { "%02x".format(it) })
            PlaybackExperiment.configureForInstrumentation(context, engine == "exo",
                PlaybackOptions(decoder = if (mode == "software") DecoderMode.SOFTWARE else DecoderMode.AUTOMATIC),
                surfaceSize = if (surfaceProtocol == "matched-1080p") 1920 to 1080 else null)
            report.put("stage", "launch_foreground")
            DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
            await(15_000) {
                activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
                    .filterIsInstance<MainActivity>().firstOrNull() }
                activity != null
            }
            onMain {
                checkNotNull(activity).window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(checkNotNull(activity), true)
                checkNotNull(activity).requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
            }
            report.put("stage", "orient_fullscreen")
            await(15_000) {
                activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
                    .filterIsInstance<MainActivity>().firstOrNull() }
                onMain { activity?.let { it.hasWindowFocus() && it.resources.configuration.orientation ==
                    android.content.res.Configuration.ORIENTATION_LANDSCAPE } == true }
            }
            val activeActivity = checkNotNull(activity)
            onMain { activeActivity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
            report.put("stage", "bind_controller")
            val activeController = onMain { MediaController.Builder(context, SessionToken(context,
                ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
            controller = activeController
            onMain {
                activeController.setMediaItem(MediaItem.Builder().setMediaId(id)
                    .setUri(FileProvider.getUriForFile(context, "${context.packageName}.resources", fixture))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Power measurement fixture")
                        .setExtras(Bundle().apply { putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, PlaybackRequestSequencer.next()) })
                        .build()).build(), 0)
                activeController.prepare()
                activeController.volume = 0f
                activeController.repeatMode = Player.REPEAT_MODE_OFF
                activeController.setPlaybackSpeed(1f)
            }
            report.put("stage", "prepare_media")
            await(25_000) { onMain { activeController.playerError?.let { throw it }
                activeController.playbackState == Player.STATE_READY && activeActivity.hasWindowFocus() } }
            onMain { activeController.play() }
            report.put("stage", "first_frame")
            await(25_000) { onMain { activeController.playerError?.let { throw it }
                activeController.isPlaying && activeController.currentPosition > 200 && activeActivity.hasWindowFocus() } }
            onMain { activeActivity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
            val surface = onMain { checkNotNull(findSurface(activeActivity.window.decorView)) }
            observedSurface = surface
            surfaceObserver = object : SurfaceHolder.Callback {
                private fun record(event: String, width: Int = 0, height: Int = 0) {
                    surfaceEvents.put(JSONObject().put("event", event).put("elapsedMs", SystemClock.elapsedRealtime())
                        .put("width", width).put("height", height))
                }
                override fun surfaceCreated(holder: SurfaceHolder) = record("created")
                override fun surfaceDestroyed(holder: SurfaceHolder) = record("destroyed")
                override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) = record("changed", width, height)
            }
            onMain { surface.holder.addCallback(checkNotNull(surfaceObserver)) }
            report.put("surfaceMonitoringVersion", 1)
            report.put("environmentBefore", environment(activeActivity))
            checkEnvironment(report.getJSONObject("environmentBefore"))
            report.put("batteryBeforeWarmup", PowerTelemetryDeviceTest.batterySample(context))
            report.put("stage", "warmup")
            val warmupStart = SystemClock.elapsedRealtime()
            traceFile.bufferedWriter().use { trace ->
                var previousHash: Int? = null
                var warmupPixelsChanged = false
                var nextTick = SystemClock.elapsedRealtime()
                repeat(warmupSeconds) { second ->
                    val row = sample(activeController, "warmup", second)
                    checkSampleConditions(row, allowExternalPower, minimumBatteryPercent)
                    trace.appendLine(row.toString())
                    if (second == warmupSeconds - 3 || second == warmupSeconds - 1) {
                        val currentHash = frameHash(surface, pixels, pixelThread)
                        if (previousHash != null && currentHash != previousHash) warmupPixelsChanged = true
                        previousHash = currentHash
                    }
                    nextTick += 1000
                    SystemClock.sleep((nextTick - SystemClock.elapsedRealtime()).coerceAtLeast(0))
                }
                report.put("warmupActualMs", SystemClock.elapsedRealtime() - warmupStart)
                report.put("warmupSurfacePixelsChanged", warmupPixelsChanged)
                check(warmupPixelsChanged) { "The actual video Surface did not show changing pixels." }
                val surfaceWidthStart = onMain { surface.holder.surfaceFrame.width() }
                val surfaceHeightStart = onMain { surface.holder.surfaceFrame.height() }
                report.put("surfaceBufferWidthStart", surfaceWidthStart).put("surfaceBufferHeightStart", surfaceHeightStart)
                if (surfaceProtocol == "matched-1080p") check(surfaceWidthStart == 1920 && surfaceHeightStart == 1080) {
                    "Matched output size was not applied to the actual Surface buffer: ${surfaceWidthStart}x$surfaceHeightStart"
                }
                val diagnosticStart = diagnostics(activeController)
                report.put("diagnosticsStart", bundleJson(diagnosticStart))
                val countersStart = exoCounters()
                report.put("exoCountersStart", countersStart)
                val actualVersion = diagnosticStart.getString("engineVersion").orEmpty()
                check(if (engine == "exo") actualVersion.startsWith("ExoPlayer ") else actualVersion.startsWith("mpv ")) {
                    "The actual engine does not match the requested engine: $actualVersion"
                }
                check(diagnosticStart.getInt("videoWidth") == 1920 && diagnosticStart.getInt("videoHeight") == 1080)
                // Media3's Matroska extractor can omit Format.frameRate. The hashed fixture manifest
                // supplies source FPS; Exo's actual rendered-frame count independently verifies it.
                if (diagnosticStart.containsKey("sourceFramesPerSecond"))
                    check(diagnosticStart.getDouble("sourceFramesPerSecond") in 23.9..24.1) { "Actual source FPS was not 24." }
                val expectedCodec = when (codec) { "avc" -> listOf("avc", "h264", "h.264")
                    "hevc", "hevc10" -> listOf("hevc", "h265", "h.265")
                    "vp9" -> listOf("vp9"); else -> listOf("av1", "av01") }
                check(expectedCodec.any { it in diagnosticStart.getString("videoCodec").orEmpty().lowercase() }) {
                    "Actual source codec does not match the fixture."
                }
                val settingsMessage = diagnosticStart.getString(PlaybackDiagnosticsContract.SETTINGS_MESSAGE).orEmpty()
                check(settingsMessage.isBlank() || (engine == "exo" && settingsMessage.startsWith("ExoPlayer + libass 实验："))) {
                    "Playback settings did not apply: $settingsMessage"
                }
                val actualDecoder = diagnosticStart.getString("decoder")
                val actualSoftware = if (engine == "exo") MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
                    .firstOrNull { it.name == actualDecoder }?.let { if (Build.VERSION.SDK_INT >= 29) it.isSoftwareOnly else null }
                    else actualDecoder?.let { it == "no" }
                report.put("actualSoftwareDecoder", actualSoftware ?: JSONObject.NULL)
                check(!actualDecoder.isNullOrBlank()) { "No actual video decoder evidence was available." }
                if (mode == "software") check(actualSoftware == true) { "The requested software path was not confirmed: $actualDecoder" }
                val samples = JSONArray()
                val frameProof = JSONArray()
                val surfaceEventStart = onMain { surfaceEvents.length() }
                val steadyStart = SystemClock.elapsedRealtime()
                report.put("stage", "steady")
                nextTick = steadyStart
                repeat(measureSeconds + 1) { second ->
                    val row = sample(activeController, "steady", second, surface)
                    checkSampleConditions(row, allowExternalPower, minimumBatteryPercent)
                    samples.put(row)
                    trace.appendLine(row.toString())
                    if (second % 61 == 0 || second == measureSeconds) {
                        frameProof.put(JSONObject().put("second", second).put("surfaceHash", frameHash(surface, pixels, pixelThread)))
                    }
                    if (second % 30 == 0) {
                        trace.flush()
                        instrumentation.sendStatus(2, Bundle().apply { putString("stream", "$label: $second/$measureSeconds seconds\n") })
                    }
                    if (second != measureSeconds) {
                        nextTick += 1000
                        SystemClock.sleep((nextTick - SystemClock.elapsedRealtime()).coerceAtLeast(0))
                    }
                }
                report.put("samples", samples).put("steadyActualMs", SystemClock.elapsedRealtime() - steadyStart)
                report.put("stage", "validate_steady")
                val diagnosticEnd = diagnostics(activeController)
                val countersEnd = exoCounters()
                report.put("diagnosticsEnd", bundleJson(diagnosticEnd)).put("exoCountersEnd", countersEnd)
                    .put("steadyFrameProof", frameProof)
                report.put("environmentAfter", environment(activeActivity))
                report.put("surfaceBufferWidth", onMain { surface.holder.surfaceFrame.width() })
                    .put("surfaceBufferHeight", onMain { surface.holder.surfaceFrame.height() })
                report.put("finalSurfaceHash", frameHash(surface, pixels, pixelThread))
                val first = samples.getJSONObject(0)
                val last = samples.getJSONObject(samples.length() - 1)
                val elapsed = last.getLong("elapsedMs") - first.getLong("elapsedMs")
                val advanced = last.getLong("positionMs") - first.getLong("positionMs")
                val issues = JSONArray()
                val steadySurfaceEvents = onMain { JSONArray().apply {
                    for (index in surfaceEventStart until surfaceEvents.length()) put(surfaceEvents.getJSONObject(index))
                } }
                report.put("steadySurfaceEvents", steadySurfaceEvents)
                if ((0 until steadySurfaceEvents.length()).any { index ->
                    val event = steadySurfaceEvents.getJSONObject(index)
                    event.getString("event") != "changed" || event.getInt("width") != surfaceWidthStart || event.getInt("height") != surfaceHeightStart
                }) issues.put("surface_recreated_or_resized_during_sampling")
                if ((0 until samples.length()).any { index ->
                    val row = samples.getJSONObject(index)
                    row.getInt("surfaceBufferWidth") != surfaceWidthStart || row.getInt("surfaceBufferHeight") != surfaceHeightStart
                }) issues.put("surface_sample_size_changed")
                if (report.getInt("surfaceBufferWidth") != surfaceWidthStart || report.getInt("surfaceBufferHeight") != surfaceHeightStart)
                    issues.put("surface_buffer_size_changed")
                for (endpoint in listOf("environmentBefore", "environmentAfter")) {
                    val env = report.getJSONObject(endpoint)
                    if (env.getInt("airplaneMode") != 1 || env.getInt("wifiOn") !in 1..2 || env.getInt("bluetoothOn") != 0) issues.put("radio_scenario_changed")
                    if (env.getInt("systemBrightness") != 1 || env.getInt("brightnessMode") != 0 || env.getDouble("windowBrightness") != -1.0) issues.put("brightness_scenario_changed")
                    if (!env.getBoolean("musicMuted") || env.getInt("musicVolume") != 0) issues.put("audio_scenario_changed")
                    if (!env.getBoolean("windowFocused")) issues.put("window_not_focused")
                    if (!isMinimumDisplayBrightness(env)) issues.put("display_brightness_not_at_hardware_minimum")
                }
                if (report.getJSONObject("environmentBefore").getDouble("refreshRate") != report.getJSONObject("environmentAfter").getDouble("refreshRate")) issues.put("refresh_rate_changed")
                if ((1 until frameProof.length()).any { frameProof.getJSONObject(it).getInt("surfaceHash") == frameProof.getJSONObject(it - 1).getInt("surfaceHash") }) issues.put("steady_surface_did_not_change")
                if (advanced.toDouble() / elapsed !in 0.98..1.02) issues.put("playback_did_not_advance_at_normal_rate")
                val stoppedSamples = (0 until samples.length()).count { !samples.getJSONObject(it).getBoolean("isPlaying") }
                if (stoppedSamples > 0) issues.put("playback_not_continuously_running")
                if (diagnosticStart.getString("decoder") != diagnosticEnd.getString("decoder")) issues.put("decoder_changed")
                if (!diagnosticStart.containsKey("droppedFrames") || !diagnosticEnd.containsKey("droppedFrames")) issues.put("dropped_frame_evidence_missing")
                if (engine == "exo") {
                    if (countersStart !is JSONObject || countersEnd !is JSONObject || !countersStart.has("renderedOutputBufferCount") || !countersEnd.has("renderedOutputBufferCount")) issues.put("rendered_frame_evidence_missing")
                    else if (countersEnd.getInt("renderedOutputBufferCount") - countersStart.getInt("renderedOutputBufferCount") < measureSeconds * 24 * 0.97) issues.put("too_few_rendered_frames")
                }
                for (key in listOf("droppedFrames", "decoderDroppedFrames")) {
                    if (diagnosticStart.containsKey(key) && diagnosticEnd.containsKey(key)) {
                        val dropped = diagnosticEnd.getLong(key) - diagnosticStart.getLong(key)
                        if (dropped > measureSeconds * 24 * 0.01) issues.put("excess_$key")
                    }
                }
                if ((0 until samples.length()).any { samples.getJSONObject(it).getInt("thermalStatus") >= 2 }) issues.put("thermal_throttling")
                if ((0 until samples.length()).any { !samples.getJSONObject(it).getBoolean("windowFocused") }) issues.put("window_not_focused_during_sampling")
                if ((0 until samples.length()).map { samples.getJSONObject(it).getDouble("displayRefreshRate") }.distinct().size != 1)
                    issues.put("display_refresh_rate_changed_during_sampling")
                report.put("qualityIssues", issues).put("playbackValidationPassed", issues.length() == 0)
                    .put("measurementQualified", !validationOnly && issues.length() == 0)
                    .put("playbackAdvanceMs", advanced).put("notPlayingSamples", stoppedSamples)
            }
            check(runBlocking { app.recentMediaRepository.findById(MediaId(id)) } == null)
            report.put("testMediaIdUnregistered", true).put("completed", true).put("stage", "complete")
        } catch (failure: Throwable) {
            primaryFailure = failure
            report.put("completed", false).put("failureType", failure.javaClass.simpleName)
                .put("failureMessage", failure.message?.take(1000))
                .put("exoDecoderFailure", io.github.joyelliot.zivplayer.platform.exoplayer.ExoFrameObservation.lastError)
            controller?.let { active -> runCatching { report.put("failurePlayerState", sample(active, "failure", -1))
                .put("failureDiagnostics", bundleJson(diagnostics(active))) } }
            activity?.let { active -> runCatching { report.put("failureEnvironment", environment(active)) } }
            throw failure
        } finally {
            val cleanupErrors = mutableListOf<Throwable>()
            fun cleanup(action: () -> Unit) {
                try { action() } catch (error: Throwable) { cleanupErrors += error }
            }
            cleanup { resultFile.writeText(report.toString(2)) }
            cleanup { onMain { surfaceObserver?.let { observedSurface?.holder?.removeCallback(it) } } }
            cleanup { onMain { controller?.stop() } }
            cleanup { onMain { controller?.clearMediaItems() } }
            cleanup { onMain { controller?.release() } }
            cleanup { onMain { activity?.finish() } }
            cleanup { context.stopService(android.content.Intent(context, PlaybackService::class.java)) }
            cleanup { pixelThread.quitSafely() }
            cleanup { pixels.recycle() }
            if (args.getString("sleepOnFailure") == "true" &&
                (!report.optBoolean("completed") || !report.optBoolean("playbackValidationPassed") || cleanupErrors.isNotEmpty())) {
                cleanup {
                    val descriptor = instrumentation.uiAutomation.executeShellCommand("input keyevent 223")
                    ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { it.readBytes() }
                }
            }
            report.put("cleanupSucceeded", cleanupErrors.isEmpty())
                .put("cleanupErrors", JSONArray(cleanupErrors.map { "${it.javaClass.simpleName}: ${it.message}" }))
            if (primaryFailure == null && cleanupErrors.isNotEmpty()) {
                report.put("completed", false).put("measurementQualified", false)
                    .put("failureType", "CleanupFailure").put("failureMessage", cleanupErrors.first().message)
            }
            cleanup { resultFile.writeText(report.toString(2)) }
            val original = primaryFailure
            if (original != null) cleanupErrors.filter { it !== original }.forEach(original::addSuppressed)
            else if (cleanupErrors.isNotEmpty()) throw cleanupErrors.first().also { first ->
                cleanupErrors.drop(1).filter { it !== first }.forEach(first::addSuppressed)
            }
        }
    }

    private fun sample(controller: MediaController, phase: String, second: Int, surface: SurfaceView? = null): JSONObject =
        PowerTelemetryDeviceTest.batterySample(context).put("phase", phase).put("second", second).also { row ->
            row.put("systemBrightness", Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS, -1))
                .put("brightnessMode", Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS_MODE, -1))
            onMain {
                surface?.let { row.put("surfaceBufferWidth", it.holder.surfaceFrame.width())
                    .put("surfaceBufferHeight", it.holder.surfaceFrame.height())
                    .put("windowFocused", it.hasWindowFocus())
                    .put("displayRefreshRate", it.display.refreshRate) }
                row.put("isPlaying", controller.isPlaying).put("positionMs", controller.currentPosition)
                    .put("bufferedPositionMs", controller.bufferedPosition).put("playbackState", controller.playbackState)
                    .put("playbackError", controller.playerError?.errorCode)
            }
        }

    private fun checkSampleConditions(row: JSONObject, allowExternalPower: Boolean, minimumBatteryPercent: Int) {
        check(allowExternalPower || (row.getInt("plugged") == 0 && row.getInt("batteryStatus") == BatteryManager.BATTERY_STATUS_DISCHARGING)) {
            "External supply or non-discharging battery status invalidates measurement."
        }
        check(row.getInt("capacityPercent") >= minimumBatteryPercent) {
            "Stop power testing below $minimumBatteryPercent percent battery."
        }
        check(row.getInt("temperatureTenthsC") <= 400) { "Battery temperature exceeded the controlled 40 C measurement range." }
        check(row.getInt("currentNowUa") != Int.MIN_VALUE) { "Current telemetry is unavailable." }
        check(row.getInt("voltageMv") in 3000..5000 && row.getInt("temperatureTenthsC") in 100..400) { "Invalid voltage/temperature telemetry." }
        check(row.getInt("systemBrightness") == 1 && row.getInt("brightnessMode") == 0) { "Screen brightness changed during sampling." }
    }

    private fun displayBrightness(): JSONObject {
        val descriptor = instrumentation.uiAutomation.executeShellCommand("dumpsys display")
        val raw = ParcelFileDescriptor.AutoCloseInputStream(descriptor).bufferedReader().use { it.readText() }
        return JSONObject().apply {
            Regex("(?m)^\\s*mCachedBrightnessInfo\\.(brightness|adjustedBrightness|brightnessMin|brightnessMax)=([0-9.Ee+-]+)")
                .findAll(raw).forEach { put(it.groupValues[1], it.groupValues[2].toDouble()) }
        }
    }

    private fun isMinimumDisplayBrightness(env: JSONObject): Boolean {
        val display = env.optJSONObject("displayBrightness") ?: return false
        return display.has("brightnessMin") && display.has("adjustedBrightness") &&
            kotlin.math.abs(display.getDouble("adjustedBrightness") - display.getDouble("brightnessMin")) < 0.000001
    }

    private fun checkEnvironment(env: JSONObject) {
        check(env.getInt("airplaneMode") == 1 && env.getInt("wifiOn") in 1..2 && env.getInt("bluetoothOn") == 0)
        check(env.getInt("systemBrightness") == 1 && env.getInt("brightnessMode") == 0 && env.getDouble("windowBrightness") == -1.0)
        check(env.getBoolean("musicMuted") && env.getInt("musicVolume") == 0 && env.getBoolean("windowFocused"))
        check(isMinimumDisplayBrightness(env)) { "Actual display brightness is not at the device minimum." }
    }

    private fun environment(activity: MainActivity): JSONObject = onMain {
        val resolver = context.contentResolver
        val audio = context.getSystemService(AudioManager::class.java)
        @Suppress("DEPRECATION")
        JSONObject().put("airplaneMode", Settings.Global.getInt(resolver, Settings.Global.AIRPLANE_MODE_ON, -1))
            .put("wifiOn", Settings.Global.getInt(resolver, Settings.Global.WIFI_ON, -1))
            .put("bluetoothOn", Settings.Global.getInt(resolver, Settings.Global.BLUETOOTH_ON, -1))
            .put("systemBrightness", Settings.System.getInt(resolver, Settings.System.SCREEN_BRIGHTNESS, -1))
            .put("brightnessMode", Settings.System.getInt(resolver, Settings.System.SCREEN_BRIGHTNESS_MODE, -1))
            .put("windowBrightness", activity.window.attributes.screenBrightness)
            .put("musicVolume", audio.getStreamVolume(AudioManager.STREAM_MUSIC))
            .put("musicMuted", audio.isStreamMute(AudioManager.STREAM_MUSIC))
            .put("refreshRate", activity.windowManager.defaultDisplay.refreshRate)
            .put("windowFocused", activity.hasWindowFocus())
    }.put("displayBrightness", displayBrightness())

    private fun diagnostics(controller: MediaController): Bundle {
        val result = onMain { controller.sendCustomCommand(SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY), Bundle.EMPTY) }
            .get(5, TimeUnit.SECONDS)
        check(result.resultCode == SessionResult.RESULT_SUCCESS)
        return result.extras
    }

    private fun exoCounters(): Any = runCatching {
        if (!PlaybackExperiment.usesExoPlayer) return JSONObject.NULL
        onMain {
            val owner = (ExoSurfaceOutput::class.java.getDeclaredField("ownerReference").apply { isAccessible = true }
                .get(null) as WeakReference<*>).get() ?: return@onMain JSONObject.NULL
            val player = owner.javaClass.getDeclaredField("player").apply { isAccessible = true }.get(owner)
            val counters = player.javaClass.getMethod("getVideoDecoderCounters").invoke(player)
            counters.javaClass.getMethod("ensureUpdated").invoke(counters)
            JSONObject().apply {
                for (name in listOf("renderedOutputBufferCount", "skippedOutputBufferCount", "droppedBufferCount", "decoderInitCount")) {
                    put(name, counters.javaClass.getField(name).get(counters))
                }
            }
        }
    }.getOrElse { JSONObject().put("unavailable", it.javaClass.simpleName) }

    @Suppress("DEPRECATION")
    private fun bundleJson(bundle: Bundle) = JSONObject().apply { bundle.keySet().forEach { put(it, bundle.get(it) ?: JSONObject.NULL) } }

    private fun frameHash(surface: SurfaceView, bitmap: Bitmap, thread: HandlerThread): Int {
        val latch = CountDownLatch(1)
        var success = false
        PixelCopy.request(surface, bitmap, { success = it == PixelCopy.SUCCESS; latch.countDown() }, Handler(thread.looper))
        check(latch.await(2, TimeUnit.SECONDS) && success) { "Actual video Surface could not be sampled." }
        val colors = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(colors, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        return colors.contentHashCode()
    }

    private fun findSurface(view: View): SurfaceView? = when (view) {
        is SurfaceView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findSurface(view.getChildAt(it)) }
        else -> null
    }

    private fun await(timeoutMs: Long, condition: () -> Boolean) {
        val end = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < end) { if (condition()) return; SystemClock.sleep(50) }
        error("Power benchmark playback readiness timed out")
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
