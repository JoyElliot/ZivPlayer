// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.SurfaceTexture
import android.os.*
import android.view.*
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModelProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.C
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Player
import androidx.media3.session.*
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.platform.playback.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** Opt-in visual evidence, run once in a fresh process. Does not register media or save preferences. */
class ClarityCaptureTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    @Test fun capturePausedFrame() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.containsKey("clarityEngine"))
        val engine = checkNotNull(args.getString("clarityEngine")).also { require(it in setOf("exo", "libmpv", "vlc")) }
        val sizing = args.getString("clarityBuffer", "default").also { require(it in setOf("default", "source", "screen")) }
        val decoderMode = args.getString("clarityDecoderMode", "automatic").also { require(it in setOf("automatic", "software")) }
        val expectedHash = checkNotNull(args.getString("clarityFixtureSha256")).also { require(it.matches(Regex("[a-f0-9]{64}"))) }
        val label = checkNotNull(args.getString("clarityLabel")).also { require(it.matches(Regex("[a-z0-9-]{1,80}"))) }
        val name = args.getString("clarityFixture", "clarity-target.mp4").also { require(it.matches(Regex("[a-z0-9-]+\\.(mp4|mkv)"))) }
        val position = args.getString("clarityPositionMs", "2000").toLong().also { require(it in 0..60_000) }
        val startPosition = args.getString("clarityStartPositionMs", "0").toLong().also { require(it in 0..60_000) }
        val regressions = args.getString("clarityRegressions", "false").toBooleanStrict()
        val pageTransitions = args.getString("clarityPageTransitions", "false").toBooleanStrict()
        require(!pageTransitions || regressions)
        val subtitles = args.getString("claritySubtitles", "false").toBooleanStrict()
        val background = args.getString("clarityBackground", "false").toBooleanStrict()
        val backgroundPlayMs = args.getString("clarityBackgroundPlayMs", "600").toLong().also { require(it in 600..6000) }
        val backgroundObserveMs = args.getString("clarityBackgroundObserveMs", "0").toLong().also { require(it in 0..5000) }
        val backgroundSeek = args.getString("clarityBackgroundSeek", "false").toBooleanStrict()
        val transientSurface = args.getString("clarityTransientSurface", "false").toBooleanStrict()
        require(!(backgroundSeek || transientSurface) || (background && engine == "vlc"))
        val subtitlePreroll = args.getString("claritySubtitlePreroll", "false").toBooleanStrict()
        val playToPosition = args.getString("clarityPlayToPosition", "false").toBooleanStrict()
        val initialTextIndex = args.getString("clarityInitialTextIndex", "-1").toInt().also { require(it in -1..1) }
        val tracksDuringRestore = args.getString("clarityTracksDuringRestore", "false").toBooleanStrict()
        val restorePlayPause = args.getString("clarityRestorePlayPause", "false").toBooleanStrict()
        require(!tracksDuringRestore || (engine == "vlc" && regressions && initialTextIndex >= 0))
        require(!restorePlayPause || tracksDuringRestore)
        val expectedGeometry = args.getString("clarityGeometry")?.let(::JSONObject)
        val fixture = File(context.filesDir, "power-fixtures/$name")
        val output = File(context.filesDir, "clarity-results/$label")
        check(!output.exists() && output.mkdirs()) { "Never overwrite an earlier capture." }
        val report = JSONObject().put("label", label).put("requestedEngine", engine).put("bufferPolicy", sizing)
            .put("requestedDecoderMode", decoderMode).put("expectedFixtureSha256", expectedHash)
            .put("fixture", name).put("positionRequestedMs", position).put("startPositionMs", startPosition).put("pid", Process.myPid())
            .put("model", Build.MODEL).put("sdk", Build.VERSION.SDK_INT)
        var activity: MainActivity? = null
        var controller: MediaController? = null
        var primaryFailure: Throwable? = null
        try {
            check(fixture.isFile)
            report.put("fixtureSha256", sha256(fixture))
            check(report.getString("fixtureSha256") == expectedHash) { "The staged fixture differs from the host reference." }
            val id = "clarity-capture-${UUID.randomUUID()}"
            val repository = (context.applicationContext as ZivPlayerApplication).recentMediaRepository
            check(runBlocking { repository.findById(MediaId(id)) } == null)
            PlaybackExperiment.configureForInstrumentation(context, engine == "exo",
                PlaybackOptions(decoder = if (decoderMode == "software") DecoderMode.SOFTWARE else DecoderMode.AUTOMATIC),
                surfaceSize = when (sizing) { "source" -> 1920 to 1080; "screen" -> 2400 to 1350; else -> null },
                libVlc = engine == "vlc")
            report.put("stage", "launch")
            DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
            await(15_000) { activity = resumedActivity(); activity != null }
            onMain {
                checkNotNull(activity).window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true)
                checkNotNull(activity).requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
            }
            await(15_000) {
                activity = resumedActivity()
                onMain { activity?.let { it.hasWindowFocus() && it.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE } == true }
            }
            val activeActivity = checkNotNull(activity)
            onMain { activeActivity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
            val active = onMain { MediaController.Builder(context,
                SessionToken(context, ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
            controller = active
            val sequence = PlaybackRequestSequencer.next()
            report.put("stage", "prepare")
            onMain {
                active.setMediaItem(MediaItem.Builder().setMediaId(id)
                    .setUri(FileProvider.getUriForFile(context, "${context.packageName}.resources", fixture))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Clarity capture fixture")
                        .setExtras(Bundle().apply {
                            putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                            putString(PlaybackRequestMetadata.TOKEN_EXTRA, "clarity-$sequence")
                        }).build())
                    .build(), startPosition)
                active.volume = 0f
                active.repeatMode = Player.REPEAT_MODE_OFF
                active.setPlaybackSpeed(1f)
                active.prepare()
            }
            await(30_000) { onMain { active.playerError?.let { throw it }; active.playbackState == Player.STATE_READY } }
            if (startPosition > 0) {
                val restored = onMain { active.currentPosition }
                report.put("preparedStartPositionMs", restored)
                check(kotlin.math.abs(restored - startPosition) < 150) { "Initial resume position: $restored instead of $startPosition" }
                if (engine == "vlc") {
                    val native = vlcState()
                    report.put("vlcPreparedStartState", native)
                    check(!native.getBoolean("nativeIsPlaying")) { "Initial READY must acknowledge native pause" }
                }
            }
            if (initialTextIndex >= 0) {
                val group = onMain { active.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }[initialTextIndex] }
                onMain { active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0)).build() }
                await(10_000) { onMain { active.currentTracks.groups.any {
                    it.mediaTrackGroup == group.mediaTrackGroup && it.isSelected
                } } }
                report.put("initialTextIndex", initialTextIndex)
            }
            require(onMain { active.duration > position }) { "Capture position must be inside the actual media duration." }
            onMain { active.play() }
            await(15_000) { onMain { active.playerError?.let { throw it }; active.isPlaying && active.currentPosition > 350 } }
            report.put("stage", "pause_seek")
            if (playToPosition) await(15_000) { onMain { active.playerError?.let { throw it }; active.currentPosition >= position } }
            onMain { active.pause(); if (!playToPosition) active.seekTo(position) }
            await(15_000) { onMain { active.playerError?.let { throw it }
                active.playbackState == Player.STATE_READY && !active.isPlaying && kotlin.math.abs(active.currentPosition - position) < 150 } }
            // Wait for the seek/frame and for the paused-controls effect before hiding the chrome.
            SystemClock.sleep(1_500)
            val surface = onMain { checkNotNull(findSurface(activeActivity.window.decorView)) }
            val geometry = onMain {
                val location = IntArray(2).also(surface::getLocationOnScreen)
                JSONObject().put("bufferWidth", surface.holder.surfaceFrame.width()).put("bufferHeight", surface.holder.surfaceFrame.height())
                    .put("viewWidth", surface.width).put("viewHeight", surface.height)
                    .put("screenX", location[0]).put("screenY", location[1]).put("windowFocused", activeActivity.hasWindowFocus())
            }
            report.put("geometry", geometry)
            report.put("windowGeometry", windowGeometry(activeActivity))
            val expectedViewWidth = expectedGeometry?.getInt("viewWidth") ?: 1920
            val expectedViewHeight = expectedGeometry?.getInt("viewHeight") ?: 1080
            check(geometry.getInt("viewWidth") == expectedViewWidth && geometry.getInt("viewHeight") == expectedViewHeight) { "Unexpected FIT viewport: $geometry" }
            val sourceBuffer = sizing != "screen"
            val expectedWidth = if (engine == "vlc") expectedViewWidth else expectedGeometry?.getInt("bufferWidth") ?: if (sourceBuffer) 1920 else 2400
            val expectedHeight = if (engine == "vlc") expectedViewHeight else expectedGeometry?.getInt("bufferHeight") ?: if (sourceBuffer) 1080 else 1350
            check(geometry.getInt("bufferWidth") == expectedWidth && geometry.getInt("bufferHeight") == expectedHeight) { "Unexpected buffer/holder geometry: $geometry" }
            check(geometry.getBoolean("windowFocused") && geometry.getInt("screenX") == (expectedGeometry?.getInt("screenX") ?: 240) &&
                geometry.getInt("screenY") == (expectedGeometry?.getInt("screenY") ?: 0)) { "Unexpected screen geometry/focus: $geometry" }
            tap(geometry.getInt("screenX") + expectedViewWidth / 2f, geometry.getInt("screenY") + expectedViewHeight / 4f)
            SystemClock.sleep(800)
            val diagnostics = onMain { active.sendCustomCommand(SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY), Bundle.EMPTY) }
                .get(5, TimeUnit.SECONDS)
            check(diagnostics.resultCode == SessionResult.RESULT_SUCCESS)
            report.put("diagnostics", bundleJson(diagnostics.extras))
            val actualVersion = diagnostics.extras.getString("engineVersion").orEmpty()
            check(actualVersion.startsWith(when (engine) { "exo" -> "ExoPlayer "; "vlc" -> "libVLC "; else -> "mpv " })) {
                "Wrong engine: $actualVersion"
            }
            check(diagnostics.extras.getInt("videoWidth") == (expectedGeometry?.getInt("sourceWidth") ?: 1920) &&
                diagnostics.extras.getInt("videoHeight") == (expectedGeometry?.getInt("sourceHeight") ?: 1080))
            report.put("stage", "capture")
            File(output, "surfaceflinger.txt").writeText(shell("dumpsys SurfaceFlinger"))
            File(output, "media-codec.txt").writeText(shell("dumpsys media.codec"))
            val childSurface = engine == "vlc" && File(output, "surfaceflinger.txt").readText()
                .substringBefore("Display 0 HWC layers:").contains(Regex("Layer \\[\\d+\\] vlc_video_control#"))
            report.put("nativeCapturePath", if (childSurface) "vlc-surface-control-child-window-readback" else "surface-pixel-copy")
            val samples = JSONArray()
            repeat(3) { index ->
                val sample = onMain { JSONObject().put("positionMs", active.currentPosition).put("isPlaying", active.isPlaying)
                    .put("playbackState", active.playbackState).put("bufferWidth", surface.holder.surfaceFrame.width())
                    .put("bufferHeight", surface.holder.surfaceFrame.height()) }
                check(!sample.getBoolean("isPlaying") && sample.getInt("playbackState") == Player.STATE_READY)
                check(sample.getInt("bufferWidth") == expectedWidth && sample.getInt("bufferHeight") == expectedHeight)
                val raw = copyBuffer(surface, sample.getInt("bufferWidth"), sample.getInt("bufferHeight"), childSurface)
                sample.put("parentSurfaceHasBuffer", raw != null)
                try { if (raw != null) {
                    val rawFile = File(output, "raw-$index.png")
                    rawFile.outputStream().use { check(raw.compress(Bitmap.CompressFormat.PNG, 100, it)) }
                    sample.put("rawSha256", sha256(rawFile))
                } } finally { raw?.recycle() }
                val screen = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                try {
                    sample.put("screenWidth", screen.width).put("screenHeight", screen.height)
                    check(screen.width == 2400 && screen.height == 1080)
                    val file = File(output, "screen-$index.png")
                    file.outputStream().use { check(screen.compress(Bitmap.CompressFormat.PNG, 100, it)) }
                    sample.put("screenSha256", sha256(file))
                } finally { screen.recycle() }
                samples.put(sample)
                SystemClock.sleep(250)
            }
            report.put("samples", samples)
            File(output, "surfaceflinger.txt").writeText(shell("dumpsys SurfaceFlinger"))
            File(output, "media-codec.txt").writeText(shell("dumpsys media.codec"))
            File(output, "logcat.txt").writeText(shell("logcat -d -v threadtime --pid ${Process.myPid()}"))
            if (subtitles) {
                report.put("stage", "subtitle_regressions")
                val snapshots = JSONArray()
                report.put("subtitleRegressions", snapshots)
                fun textGroups() = active.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }
                fun seekAndSettle() {
                    if (subtitlePreroll) {
                        val prerollPosition = (position - 1_500).coerceAtLeast(0)
                        onMain { active.seekTo(prerollPosition) }
                        await(15_000) { onMain { active.playerError?.let { throw it }; active.playbackState == Player.STATE_READY &&
                            kotlin.math.abs(active.currentPosition - prerollPosition) < 150 } }
                        onMain { active.play() }
                        await(10_000) { onMain { active.playerError?.let { throw it }; active.isPlaying && active.currentPosition >= position } }
                        onMain { active.pause() }
                        await(10_000) { onMain { !active.isPlaying && active.playbackState == Player.STATE_READY } }
                    } else {
                    val start = onMain { active.currentPosition.also { active.play() } }
                    await(10_000) { onMain { active.playerError?.let { throw it }; active.isPlaying && active.currentPosition > start + 350 } }
                    onMain { active.pause(); active.seekTo(position) }
                    await(15_000) { onMain { active.playerError?.let { throw it }; !active.isPlaying &&
                        active.playbackState == Player.STATE_READY && kotlin.math.abs(active.currentPosition - position) < 150 } }
                    }
                    SystemClock.sleep(1_500)
                    tap(geometry.getInt("screenX") + expectedViewWidth / 2f, geometry.getInt("screenY") + expectedViewHeight / 4f)
                    SystemClock.sleep(600)
                }
                fun captureSubtitle(name: String) {
                    val entry = onMain {
                        check(!active.isPlaying && !active.playWhenReady && kotlin.math.abs(active.currentPosition - position) < 150)
                        JSONObject().put("name", name).put("positionMs", active.currentPosition)
                            .put("playbackState", active.playbackState)
                            .put("videoSelected", active.currentTracks.groups.any { it.type == C.TRACK_TYPE_VIDEO && it.isSelected })
                            .put("selectedTextIds", JSONArray(textGroups().filter { it.isSelected }.map { it.getTrackFormat(0).id }))
                    }
                    if (engine == "vlc") {
                        entry.put("vlc", vlcState())
                        val subtitleSurface = onMain { checkNotNull(resumedActivity()).window.decorView
                            .findViewWithTag<SurfaceView>("vlc-subtitles-surface") }
                        val raw = copyBuffer(subtitleSurface, subtitleSurface.holder.surfaceFrame.width(),
                            subtitleSurface.holder.surfaceFrame.height(), true)
                        entry.put("subtitleSurfaceHasBuffer", raw != null)
                        try { if (raw != null) File(output, "$name-subtitle-raw.png").outputStream().use {
                            check(raw.compress(Bitmap.CompressFormat.PNG, 100, it))
                        } } finally { raw?.recycle() }
                    }
                    val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                    try { File(output, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
                    finally { bitmap.recycle() }
                    snapshots.put(entry)
                }
                fun disableSubtitles(name: String) {
                    onMain { active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
                        .clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build() }
                    await(10_000) { onMain { textGroups().none { it.isSelected } } }
                    seekAndSettle()
                    captureSubtitle(name)
                }
                val embedded = onMain { textGroups().also { check(it.size == 2) }.map { it.getTrackFormat(0).id } }
                disableSubtitles("subtitle-off-baseline")
                for ((index, kind) in listOf("ass", "srt").withIndex()) {
                    // The generated MKV manifest fixes ASS then SRT order; use its
                    // captured runtime ID so later external tracks cannot shift it.
                    onMain {
                        val group = textGroups().single { it.getTrackFormat(0).id == embedded[index] }
                        active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
                            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                            .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0)).build()
                    }
                    await(10_000) { onMain { textGroups().single { it.getTrackFormat(0).id == embedded[index] }.isSelected } }
                    seekAndSettle()
                    captureSubtitle("subtitle-embedded-$kind")
                    disableSubtitles("subtitle-embedded-$kind-off")
                }
                for (kind in listOf("ass", "srt")) {
                    val beforeIds = onMain { textGroups().map { it.getTrackFormat(0).id }.toSet() }
                    onMain { active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
                        .clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false).build() }
                    val result = onMain { active.sendCustomCommand(SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY), Bundle().apply {
                        putString(SubtitleRequestContract.URI, "content://${context.packageName}.test.fixtures/external.$kind")
                        putString(SubtitleRequestContract.MEDIA_ID, id)
                        putLong(SubtitleRequestContract.REQUEST_SEQUENCE, sequence)
                    }) }.get(15, TimeUnit.SECONDS)
                    check(result.resultCode == SessionResult.RESULT_SUCCESS) { "External $kind failed: ${result.resultCode} ${result.extras}" }
                    await(10_000) { onMain {
                        val groups = textGroups()
                        groups.size == beforeIds.size + 1 && groups.filter { it.isSelected }.singleOrNull()
                            ?.getTrackFormat(0)?.id?.let { it !in beforeIds } == true
                    } }
                    seekAndSettle()
                    captureSubtitle("subtitle-external-$kind")
                    disableSubtitles("subtitle-external-$kind-off")
                }
                File(output, "subtitles-surfaceflinger.txt").writeText(shell("dumpsys SurfaceFlinger"))
            }
            if (regressions) {
                report.put("stage", "display_regressions")
                val snapshots = JSONArray()
                report.put("displayRegressions", snapshots)
                val pausedPosition = onMain { active.currentPosition }
                fun record(name: String) {
                    report.put("displayRegressionStage", name)
                    SystemClock.sleep(2_500) // Wait beyond the OEM rotation transition, not only its mode callback.
                    await(30_000) { onMain { active.playerError?.let { throw it }; active.playbackState == Player.STATE_READY &&
                        (engine != "vlc" || !vlcState().has("surfaceRestore")) } }
                    if (engine == "vlc") {
                        val native = vlcState()
                        report.put("vlcState-$name", native)
                        check(!native.getBoolean("nativeIsPlaying")) { "Native VLC still playing after paused restore: $native" }
                    }
                    val current = checkNotNull(resumedActivity())
                    val video = onMain { checkNotNull(findSurface(current.window.decorView)) }
                    val entry = onMain {
                        check(active.playerError == null && active.playbackState == Player.STATE_READY &&
                            !active.isPlaying && !active.playWhenReady && kotlin.math.abs(active.currentPosition - pausedPosition) < 150) {
                            "$name: playing=${active.isPlaying}, intent=${active.playWhenReady}, state=${active.playbackState}, position=${active.currentPosition}, expected=$pausedPosition, error=${active.playerError}"
                        }
                        val xy = IntArray(2).also(video::getLocationOnScreen)
                        JSONObject().put("name", name).put("positionMs", active.currentPosition)
                            .put("viewWidth", video.width).put("viewHeight", video.height)
                            .put("bufferWidth", video.holder.surfaceFrame.width()).put("bufferHeight", video.holder.surfaceFrame.height())
                            .put("screenX", xy[0]).put("screenY", xy[1]).put("windowFocused", current.hasWindowFocus())
                            .put("window", windowGeometry(current))
                    }
                    check(entry.getInt("viewWidth") > 0 && entry.getInt("viewHeight") > 0)
                    snapshots.put(entry)
                    check(entry.getInt("bufferWidth") == (if (engine == "vlc") entry.getInt("viewWidth") else expectedWidth) &&
                        entry.getInt("bufferHeight") == (if (engine == "vlc") entry.getInt("viewHeight") else expectedHeight))
                    val screen = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                    try {
                        File(output, "$name.png").outputStream().use { check(screen.compress(Bitmap.CompressFormat.PNG, 100, it)) }
                        requireVisibleTarget(screen, entry)
                        requireEdgeToEdge(entry.getJSONObject("window"))
                        requireDarkCutout(screen, entry)
                    }
                    finally { screen.recycle() }
                    File(output, "$name-surfaceflinger.txt").writeText(shell("dumpsys SurfaceFlinger"))
                }
                onMain { checkNotNull(resumedActivity()).requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
                await(15_000) { onMain { resumedActivity()?.resources?.configuration?.orientation == Configuration.ORIENTATION_PORTRAIT } }
                val settings = onMain { ViewModelProvider(checkNotNull(resumedActivity()))[SettingsViewModel::class.java] }
                val original = onMain { settings.preferences.value }
                try {
                    for (fit in VideoFit.entries) {
                        // Exercise the real Compose/View fit state without writing user preferences.
                        onMain { settings.preferences.value = original.copy(options = original.options.copy(videoFit = fit)) }
                        record("portrait-${fit.name.lowercase()}")
                    }
                } finally { onMain { settings.preferences.value = original } }
                onMain { checkNotNull(resumedActivity()).requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE }
                await(15_000) { onMain { resumedActivity()?.resources?.configuration?.orientation == Configuration.ORIENTATION_LANDSCAPE } }
                record("landscape-return")
                val previousActivity = checkNotNull(resumedActivity())
                val replacementText = if (tracksDuringRestore) onMain {
                    active.currentTracks.groups.first { it.type == C.TRACK_TYPE_TEXT && !it.isSelected }
                } else null
                onMain { previousActivity.recreate() }
                if (replacementText != null) {
                    await(15_000) { onMain {
                        val before = vlcState()
                        val restoring = before.optBoolean("surfaceReopening") && !before.optBoolean("prepared") &&
                            before.optJSONArray("nativeTextTracks")?.length()?.let { count -> count > 0 } == true
                        if (restoring) {
                            check(before.getInt("nativeVolume") == 0) { "Preparation must remain muted: $before" }
                            // No main-thread prepared callback may run between the
                            // state observation and issuing this user choice.
                            report.put("trackIntentBeforeRecreatedReady", before)
                            active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
                                .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                                .setOverrideForType(TrackSelectionOverride(replacementText.mediaTrackGroup, 0)).build()
                            report.put("trackIntentRequestedId", replacementText.getTrackFormat(0).id)
                            if (restorePlayPause) {
                                active.play()
                                active.pause()
                                report.put("restorePlayThenPauseIssued", true)
                            }
                        }
                        restoring
                    } }
                }
                await(15_000) { resumedActivity()?.let { it !== previousActivity && onMain { it.hasWindowFocus() } } == true }
                await(15_000) { onMain { active.playerError?.let { throw it }; active.playbackState == Player.STATE_READY &&
                    (engine != "vlc" || !vlcState().has("surfaceRestore")) && kotlin.math.abs(active.currentPosition - pausedPosition) < 150 } }
                if (replacementText != null) await(10_000) { onMain { active.currentTracks.groups.any {
                    it.mediaTrackGroup == replacementText.mediaTrackGroup && it.isSelected
                } } }
                record("paused-recreated")
                if (replacementText != null) report.put("trackIntentPreservedAfterRestore", true)
                activity = resumedActivity()
                // Use the app's own PiP path and keep the video paused throughout.
                onMain { MainActivity::class.java.getDeclaredMethod("enterPip", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true) }
                await(15_000) { onMain { activity?.isInPictureInPictureMode == true } }
                SystemClock.sleep(2_500)
                val pip = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                try { File(output, "paused-pip.png").outputStream().use { check(pip.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
                finally { pip.recycle() }
                File(output, "paused-pip-surfaceflinger.txt").writeText(shell("dumpsys SurfaceFlinger"))
                DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
                await(15_000) { resumedActivity()?.let { onMain { !it.isInPictureInPictureMode && it.hasWindowFocus() } } == true }
                activity = resumedActivity()
                record("pip-return")
                report.put("displayRegressions", snapshots).put("pausedPipEnteredAndReturned", true)
                if (pageTransitions) repeat(2) { cycle ->
                    onMain { checkNotNull(resumedActivity()).onBackPressedDispatcher.onBackPressed() }
                    await(10_000) { findTextNode(instrumentation.uiAutomation.rootInActiveWindow, "Clarity capture fixture") != null &&
                        onMain { resumedActivity()?.let { findSurface(it.window.decorView) == null } == true } }
                    SystemClock.sleep(1_000)
                    val libraryWindow = windowGeometry(checkNotNull(resumedActivity()))
                    report.put("libraryWindow-$cycle", libraryWindow)
                    requireEdgeToEdge(libraryWindow)
                    val libraryScreen = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                    try { File(output, "library-return-$cycle.png").outputStream().use {
                        check(libraryScreen.compress(Bitmap.CompressFormat.PNG, 100, it))
                    } } finally { libraryScreen.recycle() }
                    var mini = checkNotNull(findTextNode(instrumentation.uiAutomation.rootInActiveWindow, "Clarity capture fixture"))
                    while (!mini.isClickable) mini = checkNotNull(mini.parent)
                    check(mini.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK))
                    await(15_000) { onMain { resumedActivity()?.let { findSurface(it.window.decorView) != null } == true } }
                    record("page-return-$cycle")
                    onMain { MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                        .apply { isAccessible = true }.invoke(checkNotNull(resumedActivity()), true) }
                    await(15_000) { onMain { resumedActivity()?.resources?.configuration?.orientation == Configuration.ORIENTATION_LANDSCAPE } }
                    record("page-fullscreen-$cycle")
                }
            }
            if (background) {
                report.put("stage", "background_regression")
                check(onMain { active.currentTracks.groups.any { it.type == C.TRACK_TYPE_AUDIO && it.isSelected } })
                val pausedBefore = onMain { active.currentPosition }
                val backgroundProgress = JSONObject().put("beforeMs", pausedBefore)
                report.put("backgroundProgress", backgroundProgress)
                onMain { checkNotNull(resumedActivity()).moveTaskToBack(true) }
                await(10_000) { resumedActivity() == null && (engine != "vlc" || !vlcState().optBoolean("surfacePresent")) }
                if (transientSurface) {
                    onMain {
                        val backend = checkNotNull(vlcBackend())
                        val lock = backend.javaClass.getDeclaredField("lifecycle").apply { isAccessible = true }
                            .get(backend) as kotlinx.coroutines.sync.Mutex
                        check(lock.tryLock())
                        val texture = SurfaceTexture(false)
                        val temporary = Surface(texture)
                        try {
                            // Hold the real lifecycle gate so this replacement
                            // disappears before its queued reopen can start.
                            backend.javaClass.getMethod("attachSurface", Surface::class.java).invoke(backend, temporary)
                            backend.javaClass.declaredMethods.single { it.name.startsWith("beforeSurfaceDestroyed") &&
                                it.parameterTypes.contentEquals(arrayOf(Surface::class.java)) }
                                .apply { isAccessible = true }.invoke(backend, temporary)
                        } finally {
                            lock.unlock()
                            temporary.release()
                            texture.release()
                        }
                    }
                    await(5_000) { !vlcState().getBoolean("surfaceReopenScheduled") }
                    backgroundProgress.put("afterTransientSurface", vlcState())
                }
                if (backgroundSeek) {
                    val target = pausedBefore + 500
                    onMain { active.seekTo(target) }
                    await(12_000) { onMain { active.playerError?.let { throw it }
                        active.playbackState == Player.STATE_READY && kotlin.math.abs(active.currentPosition - target) < 150 &&
                            !vlcState().has("pendingSeek") } }
                    backgroundProgress.put("seekWithoutSurfaceTargetMs", target).put("afterSeekVlc", vlcState())
                }
                if (engine == "vlc") backgroundProgress.put("beforePlayVlc", vlcState())
                val playStartedAt = SystemClock.elapsedRealtime()
                val playSamples = JSONArray()
                backgroundProgress.put("playSamples", playSamples)
                onMain { active.play() }
                await(10_000) { onMain {
                    active.playerError?.let { throw it }
                    val native = if (engine == "vlc") vlcState() else null
                    playSamples.put(JSONObject().put("elapsedMs", SystemClock.elapsedRealtime() - playStartedAt)
                        .put("positionMs", active.currentPosition).put("isPlaying", active.isPlaying)
                        .put("nativeIsPlaying", native?.optBoolean("nativeIsPlaying"))
                        .put("nativeTime", native?.opt("nativeTime"))
                        .put("bridgePositionMs", native?.opt("bridgePositionMs")))
                    active.isPlaying && active.currentPosition > pausedBefore + backgroundPlayMs &&
                        (native == null || native.optBoolean("nativeIsPlaying"))
                } }
                backgroundProgress.put("playingMs", onMain { active.currentPosition })
                val playbackElapsed = SystemClock.elapsedRealtime() - playStartedAt
                onMain { active.pause() }
                await(10_000) { onMain { !active.isPlaying && !active.playWhenReady } }
                SystemClock.sleep(500)
                backgroundProgress.put("pausedAfter500ms", onMain { active.currentPosition })
                val pauseSamples = JSONArray()
                backgroundProgress.put("pauseSamples", pauseSamples)
                val pauseObservationStarted = SystemClock.elapsedRealtime()
                while (SystemClock.elapsedRealtime() - pauseObservationStarted < backgroundObserveMs) {
                    pauseSamples.put(onMain { JSONObject().put("elapsedMs", SystemClock.elapsedRealtime() - pauseObservationStarted)
                        .put("positionMs", active.currentPosition).put("isPlaying", active.isPlaying)
                        .put("playWhenReady", active.playWhenReady) })
                    SystemClock.sleep(100)
                }
                val pausedAfter = onMain { active.currentPosition }
                backgroundProgress.put("pausedAfterMs", pausedAfter)
                val expectedStart = if (backgroundSeek) pausedBefore + 500 else pausedBefore
                check(pausedAfter >= expectedStart - 250 && pausedAfter <= expectedStart + playbackElapsed + 500) {
                    "Background clock jumped: start=$expectedStart, elapsed=$playbackElapsed, paused=$pausedAfter"
                }
                if (engine == "vlc") backgroundProgress.put("pausedVlc", vlcState())
                DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
                await(15_000) { resumedActivity()?.let { onMain { it.hasWindowFocus() } } == true }
                SystemClock.sleep(2_500)
                await(30_000) { onMain { active.playerError?.let { throw it }; active.playbackState == Player.STATE_READY &&
                    (engine != "vlc" || !vlcState().has("surfaceRestore")) } }
                activity = checkNotNull(resumedActivity())
                val returnWindow = windowGeometry(checkNotNull(activity))
                backgroundProgress.put("returnWindow", returnWindow)
                requireEdgeToEdge(returnWindow)
                val returned = onMain {
                    backgroundProgress.put("returnPositionMs", active.currentPosition)
                    check(active.playerError == null && !active.isPlaying && !active.playWhenReady &&
                        kotlin.math.abs(active.currentPosition - pausedAfter) < 200) {
                        "Background return: paused=$pausedAfter, returned=${active.currentPosition}, isPlaying=${active.isPlaying}, intent=${active.playWhenReady}, error=${active.playerError}"
                    }
                    active.currentPosition
                }
                val screen = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                try {
                    File(output, "background-return.png").outputStream().use { screen.compress(Bitmap.CompressFormat.PNG, 100, it) }
                    requireVisibleTarget(screen, geometry)
                } finally { screen.recycle() }
                report.put("backgroundRegression", JSONObject().put("beforeMs", pausedBefore).put("pausedAfterMs", pausedAfter)
                    .put("returnedMs", returned).put("playedWithoutSurface", true))
            }
            check(runBlocking { repository.findById(MediaId(id)) } == null)
            report.put("completed", true)
        } catch (error: Throwable) {
            primaryFailure = error
            report.put("failure", "${error.javaClass.name}: ${error.message}")
            runCatching { File(output, "failure-logcat.txt").writeText(shell("logcat -d -v threadtime --pid ${Process.myPid()}")) }
            runCatching { instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
                try { File(output, "failure-screen.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) } }
                finally { bitmap.recycle() }
            } }
            throw error
        } finally {
            val failures = mutableListOf<Throwable>()
            fun cleanup(action: () -> Unit) { try { action() } catch (error: Throwable) { failures += error } }
            cleanup { onMain { controller?.let { report.put("finalControllerState", JSONObject()
                .put("positionMs", it.currentPosition).put("playbackState", it.playbackState)
                .put("isPlaying", it.isPlaying).put("playWhenReady", it.playWhenReady)
                .put("error", it.playerError?.toString())) } } }
            cleanup { File(output, "final-logcat.txt").writeText(shell("logcat -d -v threadtime --pid ${Process.myPid()}")) }
            if (engine == "vlc") cleanup { report.put("vlcFinalState", vlcState()) }
            cleanup { onMain { controller?.stop(); controller?.clearMediaItems() } }
            cleanup { onMain { controller?.release() } }
            cleanup { onMain { activity?.finish() } }
            cleanup { context.stopService(Intent(context, PlaybackService::class.java)) }
            report.put("cleanupSucceeded", failures.isEmpty()).put("cleanupErrors", JSONArray(failures.map { it.toString() }))
            File(output, "capture.json").writeText(report.toString(2))
            if (primaryFailure != null) failures.forEach(checkNotNull(primaryFailure)::addSuppressed)
            else if (failures.isNotEmpty()) throw failures.first()
        }
    }

    private fun requireVisibleTarget(screen: Bitmap, geometry: JSONObject) {
        // This regression uses the generated gray target, never arbitrary media.
        // A frozen timeline over a black native output must not count as recovery.
        val left = geometry.getInt("screenX")
        val top = geometry.getInt("screenY")
        val width = geometry.getInt("viewWidth")
        val height = geometry.getInt("viewHeight")
        var visible = 0
        var sampled = 0
        for (row in 2..6) for (column in 2..6) {
            val x = left + width * column / 10
            val y = top + height * row / 10
            if (x in 0 until screen.width && y in 0 until screen.height) {
                val pixel = screen.getPixel(x, y)
                if (((pixel shr 16) and 255) + ((pixel shr 8) and 255) + (pixel and 255) > 180) visible++
                sampled++
            }
        }
        check(sampled > 0 && visible.toDouble() / sampled > 0.25) { "The paused video target is not visible: $visible/$sampled" }
    }

    private fun windowGeometry(activity: MainActivity): JSONObject = onMain {
        val decor = activity.window.decorView
        val content = activity.findViewById<View>(android.R.id.content)
        val xy = IntArray(2).also(content::getLocationOnScreen)
        val insets = androidx.core.view.ViewCompat.getRootWindowInsets(decor)
        fun rect(view: View) = JSONObject().put("width", view.width).put("height", view.height)
            .put("paddingLeft", view.paddingLeft).put("paddingTop", view.paddingTop)
            .put("paddingRight", view.paddingRight).put("paddingBottom", view.paddingBottom)
            .put("class", view.javaClass.name).put("fitsSystemWindows", view.fitsSystemWindows)
            .put("systemUiVisibility", view.systemUiVisibility)
            .put("left", view.left).put("top", view.top)
            .put("margins", (view.layoutParams as? ViewGroup.MarginLayoutParams)?.let {
                JSONArray(listOf(it.leftMargin, it.topMargin, it.rightMargin, it.bottomMargin))
            })
        val ancestors = JSONArray()
        var parent = content.parent
        while (parent is View) { ancestors.put(rect(parent)); parent = parent.parent }
        JSONObject().put("decor", rect(decor)).put("content", rect(content).put("screenX", xy[0]).put("screenY", xy[1]))
            .put("ancestors", ancestors)
            .put("cutoutMode", if (Build.VERSION.SDK_INT >= 28) activity.window.attributes.layoutInDisplayCutoutMode else -1)
            .put("statusBarsVisible", insets?.isVisible(androidx.core.view.WindowInsetsCompat.Type.statusBars()))
            .put("navigationBarsVisible", insets?.isVisible(androidx.core.view.WindowInsetsCompat.Type.navigationBars()))
            .put("safeCutoutLeft", insets?.displayCutout?.safeInsetLeft)
            .put("safeCutoutTop", insets?.displayCutout?.safeInsetTop)
            .put("safeCutoutRight", insets?.displayCutout?.safeInsetRight)
            .put("safeCutoutBottom", insets?.displayCutout?.safeInsetBottom)
    }

    private fun findTextNode(node: android.view.accessibility.AccessibilityNodeInfo?, text: String): android.view.accessibility.AccessibilityNodeInfo? {
        if (node == null) return null
        // Compose's virtual nodes need traversal; the provider's text-search API
        // is not required to implement findAccessibilityNodeInfosByText.
        if (node.text?.contains(text) == true || node.contentDescription?.contains(text) == true) return node
        for (index in 0 until node.childCount) findTextNode(node.getChild(index), text)?.let { return it }
        return null
    }

    private fun requireEdgeToEdge(window: JSONObject) {
        val decor = window.getJSONObject("decor")
        val content = window.getJSONObject("content")
        check(content.getInt("width") == decor.getInt("width") && content.getInt("height") == decor.getInt("height") &&
            content.getInt("screenX") == 0 && content.getInt("screenY") == 0) {
            "Window content has an unintended inset: $window"
        }
    }

    private fun requireDarkCutout(screen: Bitmap, entry: JSONObject) {
        val cutout = entry.getJSONObject("window").optInt("safeCutoutLeft")
        if (cutout <= 0 || entry.getInt("screenX") < cutout || screen.width <= screen.height) return
        var bright = 0
        var sampled = 0
        for (y in 0 until screen.height step 4) for (x in 0 until cutout step 4) {
            val color = screen.getPixel(x, y)
            if (((color shr 16) and 255) > 240 && ((color shr 8) and 255) > 240 && (color and 255) > 240) bright++
            sampled++
        }
        val fraction = bright.toDouble() / sampled
        entry.put("cutoutWhiteFraction", fraction)
        check(fraction < 0.01) { "White player cutout background: $bright/$sampled samples" }
    }

    private fun vlcBackend(): Any? {
        val output = Class.forName("io.github.joyelliot.zivplayer.platform.libvlc.VlcSurfaceOutput")
        val reference = output.getDeclaredField("reference").apply { isAccessible = true }.get(null) as java.lang.ref.WeakReference<*>
        return reference.get()
    }
    private fun vlcState(): JSONObject = onMain {
        val backend = vlcBackend() ?: return@onMain JSONObject().put("backendPresent", false)
        fun field(name: String) = backend.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(backend)
        val player = field("player")
        val seekBridge = field("bridge")
        val video = field("videoSurface") as? Surface
        val textTracks = player?.javaClass?.getMethod("getTracks", Int::class.javaPrimitiveType)?.invoke(player, 2) as? Array<*>
        JSONObject().put("backendPresent", true).put("surfacePresent", video != null).put("surfaceValid", video?.isValid)
            .put("nativeTextTracks", JSONArray(textTracks?.map { track -> JSONObject()
                .put("id", track?.javaClass?.getField("id")?.get(track))
                .put("selected", track?.javaClass?.getField("selected")?.get(track)) } ?: emptyList<JSONObject>()))
            .put("leasePresent", field("lease") != null).put("surfaceRestore", field("surfaceRestore")?.toString())
            .put("surfaceWidth", field("surfaceWidth")).put("surfaceHeight", field("surfaceHeight"))
            .put("directVideoOutput", field("directVideoOutput"))
            .put("prepared", field("prepared")).put("initializingSeek", field("initializingSeek"))
            .put("initializingPause", field("initializingPause"))
            .put("surfaceReopening", field("surfaceReopening"))
            .put("surfaceReopenScheduled", field("surfaceReopenScheduled"))
            .put("nativeTime", player?.javaClass?.getMethod("getTime")?.invoke(player))
            .put("bridgePositionMs", seekBridge?.javaClass?.getMethod("positionMs")?.invoke(seekBridge))
            .put("nativeIsPlaying", player?.javaClass?.getMethod("isPlaying")?.invoke(player))
            .put("nativeVolume", player?.javaClass?.getMethod("getVolume")?.invoke(player))
            .put("nativeVideoSelected", player?.javaClass?.getMethod("getSelectedTrack", Int::class.javaPrimitiveType)?.invoke(player, 1) != null)
            .put("pendingSeek", field("pendingSeek")?.toString())
    }

    private fun copyBuffer(surface: SurfaceView, width: Int, height: Int, childSurface: Boolean): Bitmap? {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val result = CompletableFuture<Int>()
        try {
            PixelCopy.request(surface.holder.surface, bitmap, { result.complete(it) }, Handler(Looper.getMainLooper()))
            val code = result.get(5, TimeUnit.SECONDS)
            if (childSurface && code == PixelCopy.ERROR_SOURCE_NO_DATA) {
                bitmap.recycle()
                return null // VLC owns a child SurfaceControl; the UI parent's queue is empty.
            }
            check(code == PixelCopy.SUCCESS) { "Video Surface PixelCopy failed: $code" }
            return bitmap
        } catch (error: Throwable) {
            if (result.isDone) bitmap.recycle() else result.whenComplete { _, _ -> bitmap.recycle() }
            throw error
        }
    }
    private fun tap(x: Float, y: Float) {
        val down = SystemClock.uptimeMillis()
        for ((action, time) in listOf(MotionEvent.ACTION_DOWN to down, MotionEvent.ACTION_UP to (down + 60))) {
            val event = MotionEvent.obtain(down, time, action, x, y, 0).apply { source = InputDevice.SOURCE_TOUCHSCREEN }
            try { check(instrumentation.uiAutomation.injectInputEvent(event, true)) } finally { event.recycle() }
        }
    }
    private fun resumedActivity(): MainActivity? = onMain {
        ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).filterIsInstance<MainActivity>().firstOrNull()
    }
    private fun findSurface(view: View): SurfaceView? = when (view) {
        is SurfaceView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findSurface(view.getChildAt(it)) }
        else -> null
    }
    private fun await(timeoutMs: Long, predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < deadline) { if (predicate()) return; SystemClock.sleep(50) }
        error("Clarity capture readiness timed out")
    }
    private fun <T> onMain(action: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return action()
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
    private fun shell(command: String): String = ParcelFileDescriptor.AutoCloseInputStream(
        instrumentation.uiAutomation.executeShellCommand(command)).bufferedReader().use { it.readText() }
    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered().use { stream ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val count = stream.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
    @Suppress("DEPRECATION")
    private fun bundleJson(bundle: Bundle) = JSONObject().apply { bundle.keySet().forEach { put(it, bundle.get(it) ?: JSONObject.NULL) } }
}
