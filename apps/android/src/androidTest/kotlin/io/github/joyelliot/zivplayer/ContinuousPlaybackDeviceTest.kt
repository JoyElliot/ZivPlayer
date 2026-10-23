// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.media.AudioManager
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import io.github.joyelliot.zivplayer.platform.playback.PlaybackDiagnosticsContract
import io.github.joyelliot.zivplayer.data.media.toDiagnosticJson
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.model.VideoFit
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.concurrent.TimeUnit
import io.github.joyelliot.zivplayer.feature.player.R as PlayerR

/** Explicit opt-in; host must back up personal data before running and restore it afterwards. */
class ContinuousPlaybackDeviceTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as ZivPlayerApplication
    @Volatile private var phase = "startup"
    private val observations = JSONArray()
    private val report = JSONObject().put("completed", false).put("observations", observations)
    private val output get() = File(app.cacheDir, "continuous-device-result.json")
    private fun vm() = ViewModelProvider(activity.activity)[PlaybackControllerViewModel::class.java]
    private fun area() = activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_video_area))
    private fun mini() = activity.onNodeWithTag("mini_player", useUnmergedTree = true)
    private fun timeline() = activity.onNode(SemanticsMatcher.keyIsDefined(SemanticsActions.SetProgress))

    @Test fun realFramesSurvivePresentationAndScrubbingUsesSystemVolume() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("continuousDeviceValidation") == "true")
        val original = runBlocking { app.playerPreferences.preferences.first() }
        val audio = app.getSystemService(AudioManager::class.java)
        val originalVolume = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
        val future = onMain { MediaController.Builder(app,
            SessionToken(app, ComponentName(app, PlaybackService::class.java))).buildAsync() }
        val controller = future.get(15, TimeUnit.SECONDS)
        activity.waitForIdle()
        val playbackEvents = JSONArray()
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playbackEvents.put(JSONObject().put("elapsedMs", SystemClock.elapsedRealtime())
                    .put("isPlaying", isPlaying).put("playWhenReady", controller.playWhenReady)
                    .put("phase", phase).put("positionMs", controller.currentPosition))
            }
        }
        try {
            runBlocking { app.playerPreferences.update { it.copy(backgroundPlayback = false,
                autoPictureInPicture = false, options = it.options.copy(videoFit = VideoFit.FIT)) } }
            val sequence = PlaybackRequestSequencer.next()
            onMain {
                controller.addListener(listener)
                controller.setMediaItem(MediaItem.Builder().setMediaId("continuous-device-fixture")
                    .setUri(Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/continuous-av.mp4"))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Continuous device fixture")
                        .setExtras(Bundle().apply {
                            putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                            putString(PlaybackRequestMetadata.TOKEN_EXTRA, "continuous-$sequence")
                        }).build()).build())
                controller.repeatMode = Player.REPEAT_MODE_ONE
                controller.prepare()
            }
            await("native prepared tracks") { controller.currentTracks.groups.isNotEmpty() }
            onMain { controller.play() }
            await("initial native playback") { controller.isPlaying && controller.currentPosition > 400 && vm().playerState.value.hasVideo }
            val diagnostics = onMain { controller.sendCustomCommand(
                SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY), Bundle.EMPTY) }.get(5, TimeUnit.SECONDS)
            if (diagnostics.resultCode == SessionResult.RESULT_SUCCESS) report.put("diagnostics",
                JSONObject(PlaybackDiagnosticsContract.decode(diagnostics.extras).toDiagnosticJson(System.currentTimeMillis(), System.currentTimeMillis())))
            app.contentResolver.openInputStream(Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/continuous-av.mp4"))!!.use { stream ->
                val hash = java.security.MessageDigest.getInstance("SHA-256")
                val buffer = ByteArray(8192)
                while (true) { val count = stream.read(buffer); if (count < 0) break; hash.update(buffer, 0, count) }
                report.put("fixtureSha256", hash.digest().joinToString("") { "%02x".format(it) })
            }
            val texture = onMain { checkNotNull(findTexture(activity.activity.window.decorView)) }
            await("surface available") { texture.isAvailable }
            val surface = onMain { texture.surfaceTexture }
            fun assertSurface(label: String) {
                onMain {
                    assertSame(label, texture, findTexture(activity.activity.window.decorView))
                    assertSame(label, surface, texture.surfaceTexture)
                    assertTrue(label, controller.playWhenReady)
                    assertNull(controller.playerError)
                }
            }
            assertLiveFrames("mini-live", texture)
            val entryOrientation = onMain { activity.activity.resources.configuration.orientation }
            mini().performTouchInput { click(Offset(width * 0.5f, height * 0.45f)) }
            await("expanded controls") { vm().playerState.value.canSeek }
            await("landscape video opens in landscape by default") {
                activity.activity.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE
            }
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_rotate)).assertIsDisplayed()
            assertSurface("mini to expanded")
            assertLiveFrames("expanded-live", texture)
            capture("expanded")

            val windowHeight = onMain { maxOf(activity.activity.window.decorView.height, activity.activity.window.decorView.width).toFloat() }
            val density = app.resources.displayMetrics.density
            val orientation = onMain { activity.activity.resources.configuration.orientation }
            button(PlayerR.string.player_rotate).performTouchInput { click() }
            await("custom rotation hands off to system orientation") {
                activity.activity.resources.configuration.orientation != orientation
            }
            assertSurface("rotated")
            revealControls()
            button(PlayerR.string.player_quick_menu).performTouchInput { click() }
            button(PlayerR.string.player_close_menu).assertIsDisplayed().performTouchInput { click() }
            capture("rotated-menu-closed")
            revealControls()
            button(PlayerR.string.player_rotate).performClick()
            await("manual rotation returns to landscape") {
                activity.activity.resources.configuration.orientation == orientation
            }

            // Collapse directly from landscape: no separate full-size portrait stage.
            activity.onRoot().performTouchInput {
                swipe(Offset(width / 2f, 11f * density), Offset(width / 2f, height * 0.85f), 600)
            }
            await("combined collapse restores the entry window") {
                activity.activity.resources.configuration.orientation == entryOrientation
            }
            activity.waitUntil(5_000) { activity.onAllNodesWithTag("mini_player", useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }
            mini().assertExists()
            assertSurface("collapsed")
            assertLiveFrames("mini-after-collapse", texture)
            capture("mini")
            mini().performTouchInput {
                down(center)
                moveTo(center + Offset(0f, -windowHeight * 0.14f), 200)
                moveTo(center + Offset(0f, -windowHeight * 0.02f), 200)
                up()
            }
            SystemClock.sleep(650)
            mini().assertExists()
            assertSurface("reversed mini drag")
            mini().performTouchInput { swipe(center, center + Offset(0f, -windowHeight * 0.75f), 700) }
            await("drag expansion chooses landscape again") {
                activity.activity.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE
            }
            button(PlayerR.string.player_rotate).assertIsDisplayed()
            assertSurface("drag expanded")

            // Enter/return through the actual system PiP window.
            revealControls()
            button(PlayerR.string.player_quick_menu).performClick()
            activity.onNodeWithText(app.getString(PlayerR.string.player_pip)).performScrollTo().performClick()
            await("system PiP entry") { activity.activity.isInPictureInPictureMode }
            SystemClock.sleep(800)
            assertSurface("PiP")
            assertLiveFrames("pip-live", texture)
            capture("pip")
            DeviceForegroundRule.startActivity("${app.packageName}/.MainActivity")
            await("PiP return") { !activity.activity.isInPictureInPictureMode }
            SystemClock.sleep(800)
            assertSurface("PiP return")

            phase = "automatic-pip"
            runBlocking { app.playerPreferences.update { it.copy(autoPictureInPicture = true) } }
            await("automatic PiP setting") { ViewModelProvider(activity.activity)[SettingsViewModel::class.java].preferences.value.autoPictureInPicture }
            shell("input keyevent KEYCODE_HOME")
            await("automatic PiP entry") { activity.activity.isInPictureInPictureMode }
            SystemClock.sleep(650)
            assertSurface("automatic PiP")
            assertLiveFrames("automatic-pip-live", texture)
            capture("automatic-pip")
            DeviceForegroundRule.startActivity("${app.packageName}/.MainActivity")
            await("automatic PiP return") { !activity.activity.isInPictureInPictureMode }
            assertSurface("automatic PiP return")
            val interruptions = (0 until playbackEvents.length()).map { playbackEvents.getJSONObject(it) }
                .filter { !it.getBoolean("isPlaying") && it.getBoolean("playWhenReady") }
            report.put("playingStateInterruptionsDuringHandoffs", interruptions.size)
            assertTrue("presentation should not interrupt playing state: $interruptions", interruptions.isEmpty())
            phase = "paused-scrub"
            onMain { controller.pause(); vm().seekTo(0) }
            await("paused beginning") { !controller.playWhenReady && controller.currentPosition < 200 }
            SystemClock.sleep(400)
            revealControls()
            val previewStart = frameStamp(texture)
            val previewHash = frameHash(texture)
            val startedAt = SystemClock.elapsedRealtime()
            timeline().performTouchInput {
                down(Offset(width * 0.1f, center.y))
                moveTo(Offset(width * 0.94f, center.y), 200)
            }
            await("first frame before releasing timeline") { frameStamp(texture) != previewStart && frameHash(texture) != previewHash }
            val firstPreviewMs = SystemClock.elapsedRealtime() - startedAt
            assertFalse(onMain { controller.playWhenReady })
            val nextStamp = frameStamp(texture)
            val nextHash = frameHash(texture)
            timeline().performTouchInput { moveTo(Offset(width * 0.48f, center.y), 200) }
            await("reverse frame before releasing timeline") { frameStamp(texture) != nextStamp && frameHash(texture) != nextHash }
            assertFalse(onMain { controller.playWhenReady })
            val finalPreviewHash = frameHash(texture)
            val finalStartedAt = SystemClock.elapsedRealtime()
            timeline().performTouchInput { up() }
            await("final exact position and presented frame") { kotlin.math.abs(controller.currentPosition - controller.duration * 0.48) < 350 && frameHash(texture) != finalPreviewHash }
            observations.put(JSONObject().put("stage", "paused-scrub")
                .put("firstPresentedPreviewMs", firstPreviewMs)
                .put("finalControllerPositionMs", onMain { controller.currentPosition })
                .put("releaseToFinalPositionMs", SystemClock.elapsedRealtime() - finalStartedAt))
            capture("paused-after-scrub")

            val minimum = audio.getStreamMinVolume(AudioManager.STREAM_MUSIC)
            val maximum = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
            if (!audio.isVolumeFixed && maximum - minimum >= 4) {
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, minimum + 1, 0)
                val level = 2.35f / (maximum - minimum)
                onMain { vm().setVolume(level) }
                await("system volume write") { audio.getStreamVolume(AudioManager.STREAM_MUSIC) == minimum + 3 }
                val gain = onMain { controller.volume }
                assertTrue(gain in 0f..1f)
                // Some system volume panels use the first key only to reveal themselves.
                val attempts = JSONArray()
                for (attempt in 1..3) {
                    shell("input keyevent KEYCODE_VOLUME_UP")
                    SystemClock.sleep(400)
                    activity.waitForIdle()
                    val index = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
                    attempts.put(index)
                    if (index > minimum + 3) break
                }
                assertTrue("hardware key eventually changes system volume: $attempts",
                    audio.getStreamVolume(AudioManager.STREAM_MUSIC) > minimum + 3)
                await("hardware volume is reflected in UI") {
                    controller.volume == 1f && kotlin.math.abs(vm().playerState.value.volume -
                        (audio.getStreamVolume(AudioManager.STREAM_MUSIC) - minimum).toFloat() / (maximum - minimum)) < 0.01f
                }
                observations.put(JSONObject().put("stage", "system-volume").put("hardwareKeyIndices", attempts)
                    .put("minimum", minimum).put("maximum", maximum)
                    .put("interpolationApplicable", maximum - minimum < 50).put("interpolatedGain", gain)
                    .put("afterHardwareKeyIndex", audio.getStreamVolume(AudioManager.STREAM_MUSIC)))
            }
            // Recreate only after testing same-Surface presentation paths: recreation is a new View owner.
            activity.activityRule.scenario.recreate()
            await("paused Activity recreation") { !vm().playerState.value.playWhenReady && vm().playerState.value.hasVideo }
            val recreated = onMain { checkNotNull(findTexture(activity.activity.window.decorView)) }
            await("recreated paused surface") { recreated.isAvailable && frameStamp(recreated) > 0 }
            capture("recreated-paused")
            report.put("completed", true)
        } catch (failure: Throwable) {
            report.put("failure", failure.stackTraceToString())
            report.put("failureState", onMain { "controllerState=${controller.playbackState}, position=${controller.currentPosition}, error=${controller.playerError}, ui=${vm().playerState.value}" })
            capture("failure")
            throw failure
        } finally {
            onMain { controller.removeListener(listener); controller.stop(); controller.release() }
            runBlocking { app.playerPreferences.update { original } }
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, originalVolume, 0)
            report.put("playbackEvents", playbackEvents)
            output.writeText(report.toString(2))
        }
    }

    private fun button(id: Int) = activity.onNodeWithContentDescription(app.getString(id))
    private fun revealControls() {
        if (activity.onAllNodesWithContentDescription(app.getString(PlayerR.string.player_quick_menu)).fetchSemanticsNodes().isEmpty()) {
            area().performTouchInput { click(Offset(width * 0.5f, height * 0.35f)) }
            SystemClock.sleep(400)
        }
    }
    private fun frameStamp(texture: TextureView): Long = onMain { texture.surfaceTexture?.timestamp ?: 0 }
    private fun frameHash(texture: TextureView): Int = onMain {
        val bitmap = checkNotNull(texture.getBitmap(96, 54))
        val pixels = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        bitmap.recycle()
        pixels.contentHashCode()
    }
    private fun assertLiveFrames(label: String, texture: TextureView) {
        phase = label
        val stamps = mutableSetOf<Long>()
        val hashes = mutableSetOf<Int>()
        repeat(5) {
            onMain {
                stamps += texture.surfaceTexture?.timestamp ?: 0
                val bitmap = checkNotNull(texture.getBitmap(96, 54))
                val pixels = IntArray(bitmap.width * bitmap.height)
                bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                assertTrue("$label non-black content", pixels.toSet().size > 8)
                hashes += pixels.contentHashCode()
                bitmap.recycle()
            }
            SystemClock.sleep(160)
        }
        observations.put(JSONObject().put("stage", label).put("timestamps", stamps.size).put("pixelHashes", hashes.size))
        assertTrue("$label receives new frames", stamps.size > 1)
        assertTrue("$label pixels change", hashes.size > 1)
    }
    private fun capture(label: String) {
        val bitmap = instrumentation.uiAutomation.takeScreenshot() ?: return
        File(app.cacheDir, "continuous-$label.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }
    private fun await(label: String, predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 12_000
        while (SystemClock.elapsedRealtime() < deadline) {
            activity.waitForIdle()
            if (onMain(predicate)) return
            SystemClock.sleep(30)
        }
        error("Timed out: $label")
    }
    private fun <T> onMain(action: () -> T): T {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) return action()
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
    private fun shell(command: String) {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use { it.readBytes() }
    }
    private fun findTexture(view: View): TextureView? {
        if (view is TextureView) return view
        if (view is ViewGroup) for (i in 0 until view.childCount) findTexture(view.getChildAt(i))?.let { return it }
        return null
    }
}
