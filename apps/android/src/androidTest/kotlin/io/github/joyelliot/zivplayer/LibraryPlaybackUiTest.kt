// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.graphics.Bitmap
import android.graphics.Color
import android.os.SystemClock
import android.view.WindowManager
import android.view.View
import android.view.ViewGroup
import android.view.TextureView
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.media3.common.Player
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.model.VideoFit
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import kotlin.math.min
import kotlin.math.abs
import io.github.joyelliot.zivplayer.feature.library.R as LibraryR
import io.github.joyelliot.zivplayer.feature.player.R as PlayerR

/** Uses only the existing, explicitly granted generated-fixture folder. */
class LibraryPlaybackUiTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as ZivPlayerApplication

    @Test fun folderSearchPlayAllFullscreenGesturesAndQueueSelection() {
        val folderName = checkNotNull(InstrumentationRegistry.getArguments().getString("personalFixtureFolder"))
        val folder = runBlocking { app.mediaRepositories.library.observeFolders().first().single { it.name == folderName } }
        val expected = runBlocking { app.mediaRepositories.library.observeMedia().first() }
            .filter { it.folderId == folder.id && !it.hidden && it.title.contains("tracks-", true) }
            .sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.title })
        assertEquals(2, expected.size)
        val vm = onMain { ViewModelProvider(activity.activity)[PlaybackControllerViewModel::class.java] }
        val settings = onMain { ViewModelProvider(activity.activity)[SettingsViewModel::class.java] }
        val originalVideoFit = runBlocking { app.playerPreferences.preferences.first().options.videoFit }
        val originalBrightness = onMain { activity.activity.window.attributes.screenBrightness }
        val originalVolume = onMain { vm.playerState.value.volume }
        onMain { activity.activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
        try {
            await("settings loaded") { settings.loaded.value }
            onMain { settings.update { it.copy(options = it.options.copy(videoFit = VideoFit.FIT)) } }
            val directoryName = expected.first().relativePath.substringBeforeLast('/', "").substringAfterLast('/').ifBlank { folderName }
            activity.onNodeWithText(directoryName).performScrollTo().performClick()
            activity.onNodeWithContentDescription(app.getString(LibraryR.string.library_search)).performClick()
            activity.onNode(hasSetTextAction()).performTextInput("tracks-")
            activity.waitUntil(5_000) { activity.onAllNodesWithText(expected.last().title).fetchSemanticsNodes().isNotEmpty() }
            capture("product-folder-filtered.png")
            activity.onNodeWithContentDescription(app.getString(LibraryR.string.library_play_all)).performClick()
            await("folder queue playing") { vm.playerState.value.let { it.queue.size == 2 && it.playWhenReady && it.positionMs > 300L && it.title == expected.first().title } }
            await("fullscreen control enabled") { vm.playerState.value.let { it.canRenderVideo && it.hasVideo } }
            onMain { vm.seekTo(0) }
            await("generated fixture starts before gesture checks") { vm.playerState.value.positionMs < 1_000L }
            val portraitVideo = activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_video_area))
            portraitVideo.performTouchInput { swipe(Offset(width * 0.15f, height * 0.65f), Offset(width * 0.15f, height * 0.4f), 300) }
            await("left swipe changes only the player window brightness") { activity.activity.window.attributes.screenBrightness in 0.01f..1f }
            portraitVideo.performTouchInput { swipe(Offset(width * 0.85f, height * 0.3f), Offset(width * 0.85f, height * 0.55f), 300) }
            await("right swipe reaches the playback controller volume") { vm.playerState.value.volume < originalVolume }
            onMain { vm.setVolume(originalVolume) }
            onMain { vm.pause() }
            await("pause before first rotation") { !vm.playerState.value.playWhenReady }
            SystemClock.sleep(750)
            rotatePaused(vm, android.content.res.Configuration.ORIENTATION_LANDSCAPE)
            onMain { vm.togglePlayPause() }
            await("resume after first paused rotation") { vm.playerState.value.playWhenReady }
            val originalSpeed = onMain { vm.playerState.value.playbackSpeed }
            val video = activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_video_area))
            video.performTouchInput { down(Offset(width * 0.6f, height * 0.45f)) }
            await("actual service receives held 2x") { vm.controller.value?.playbackParameters?.speed == 2f }
            video.performTouchInput { moveTo(Offset(width * 0.05f, height * 0.45f)) }
            await("actual service receives held slow motion") { vm.controller.value?.playbackParameters?.speed == 0.25f }
            capture("product-fullscreen-slow-motion.png")
            video.performTouchInput { up() }
            await("actual release restores original speed") { vm.controller.value?.playbackParameters?.speed == originalSpeed }
            video.performTouchInput { doubleClick(Offset(width * 0.5f, height * 0.35f)) }
            await("actual double tap pauses") { !vm.playerState.value.playWhenReady }
            capture("product-fullscreen-controls.png")
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_quick_menu)).performClick()
            capture("product-fullscreen-menu.png")
            activity.onNodeWithText(app.getString(PlayerR.string.player_speed_title)).performClick()
            activity.onNodeWithText("1.5×").performClick()
            await("quick menu speed choice") { vm.controller.value?.playbackParameters?.speed == 1.5f }
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_back)).performClick()
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_close_menu)).performClick()
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_queue)).performClick()
            capture("product-fullscreen-queue.png")
            activity.onNodeWithText(expected.last().title).performClick()
            await("queue selection plays second") { vm.playerState.value.let { it.title == expected.last().title && it.playWhenReady && it.hasVideo && it.positionMs > 300L && (it.durationMs ?: 0L) > 0L } }
            onMain { vm.pause() }
            await("pause selected item for portrait layout review") { !vm.playerState.value.playWhenReady }
            SystemClock.sleep(750) // Let queued video frames drain before testing an idle paused VO.
            rotatePaused(vm, android.content.res.Configuration.ORIENTATION_PORTRAIT)
            capture("product-player-portrait.png")
            assertPortraitFitModes(settings)
            rotatePaused(vm, android.content.res.Configuration.ORIENTATION_LANDSCAPE)
            capture("product-paused-landscape.png")
            activity.onNodeWithContentDescription(app.getString(LibraryR.string.library_back_folders)).performClick()
            await("returning to the library restores the window brightness") { activity.activity.window.attributes.screenBrightness == originalBrightness }
            activity.onNode(hasSetTextAction()).assertTextContains("tracks-")
            activity.onNodeWithContentDescription(app.getString(LibraryR.string.library_play_all)).assertIsDisplayed()
        } finally {
            onMain { vm.cancelTemporarySpeed(); vm.setVolume(originalVolume); vm.stop() }
            runBlocking { app.playerPreferences.update { it.copy(options = it.options.copy(videoFit = originalVideoFit)) } }
        }
    }

    private fun capture(name: String) {
        activity.waitForIdle()
        SystemClock.sleep(250)
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        try { File(app.cacheDir, name).outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
    }

    private data class PausedFrame(val identity: String?, val pixels: List<Int>)

    private fun assertPortraitFitModes(settings: SettingsViewModel) {
        // The generated six-color bars distinguish cropping from stretching.
        for (fit in listOf(VideoFit.FILL, VideoFit.STRETCH, VideoFit.FIT)) {
            onMain { settings.update { it.copy(options = it.options.copy(videoFit = fit)) } }
            val deadline = SystemClock.uptimeMillis() + 5_000L
            var valid: Boolean
            do {
                activity.waitForIdle()
                val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
                try {
                    fun saturated(y: Float) = (1..9).count { x ->
                        val pixel = bitmap.getPixel(bitmap.width * x / 10, (bitmap.height * y).toInt())
                        val channels = listOf(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
                        channels.max() - channels.min() > 80
                    }
                    val colors = (1..19).mapNotNull { x ->
                        val pixel = bitmap.getPixel(bitmap.width * x / 20, bitmap.height / 2)
                        val r = Color.red(pixel); val g = Color.green(pixel); val b = Color.blue(pixel)
                        if (maxOf(r, g, b) - minOf(r, g, b) > 80)
                            (if (r > 120) 4 else 0) + (if (g > 120) 2 else 0) + (if (b > 120) 1 else 0)
                        else null
                    }.toSet()
                    valid = when (fit) {
                        VideoFit.FIT -> saturated(0.2f) <= 1 && saturated(0.8f) <= 1 && colors.size >= 4
                        VideoFit.FILL -> saturated(0.2f) >= 4 && saturated(0.8f) >= 4 && colors.size <= 3
                        VideoFit.STRETCH -> saturated(0.2f) >= 4 && saturated(0.8f) >= 4 && colors.size >= 4
                    }
                } finally { bitmap.recycle() }
                if (!valid) SystemClock.sleep(100)
            } while (!valid && SystemClock.uptimeMillis() < deadline)
            capture("product-video-fit-${fit.name.lowercase()}.png")
            assertTrue("The actual paused picture must match $fit", valid)
        }
    }

    private fun rotatePaused(vm: PlaybackControllerViewModel, orientation: Int) {
        val expected = pausedFrame(vm)
        var resumed = false
        val seeks = mutableListOf<Int>()
        val controller = onMain { checkNotNull(vm.controller.value) }
        val listener = object : Player.Listener {
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (playWhenReady) resumed = true
            }

            override fun onPositionDiscontinuity(oldPosition: Player.PositionInfo, newPosition: Player.PositionInfo, reason: Int) {
                if (reason == Player.DISCONTINUITY_REASON_SEEK || reason == Player.DISCONTINUITY_REASON_SEEK_ADJUSTMENT) seeks += reason
            }
        }
        onMain { controller.addListener(listener) }
        try {
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_rotate)).assertIsEnabled().performClick()
            await("paused rotation attached") { activity.activity.resources.configuration.orientation == orientation }
            SystemClock.sleep(1_000) // Configuration changes precede the system rotation animation.
            assertPausedFixtureFillsVideoBounds(vm, expected)
            onMain {
                assertTrue("Paused rotation must never resume playback", !resumed)
                assertTrue("Paused rotation must not seek (events=$seeks)", seeks.isEmpty())
            }
        } finally { onMain { controller.removeListener(listener) } }
    }

    private fun framePixels(bitmap: Bitmap): List<Int> {
        val width = min(bitmap.width.toFloat(), bitmap.height * 16f / 9f)
        val height = width * 9f / 16f
        val left = (bitmap.width - width) / 2f
        val top = (bitmap.height - height) / 2f
        return (2..18).flatMap { y ->
            (2..38).map { x -> bitmap.getPixel((left + width * x / 40f).toInt(), (top + height * y / 20f).toInt()) }
        }
    }

    private fun pausedFrame(vm: PlaybackControllerViewModel): PausedFrame {
        activity.waitForIdle()
        val pixels = nativeFramePixels()
        return onMain { vm.playerState.value.let { PausedFrame(it.playbackIdentity, pixels) } }
    }

    private fun nativeFramePixels(): List<Int> {
        fun findTexture(view: View): TextureView? = when (view) {
            is TextureView -> view
            is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findTexture(view.getChildAt(it)) }
            else -> null
        }
        val bitmap = onMain { checkNotNull(checkNotNull(findTexture(activity.activity.window.decorView)).bitmap) }
        return try { framePixels(bitmap) } finally { bitmap.recycle() }
    }

    private fun assertPausedFixtureFillsVideoBounds(vm: PlaybackControllerViewModel, expected: PausedFrame) {
        activity.waitForIdle()
        val deadline = SystemClock.uptimeMillis() + 5_000L
        var valid: Boolean
        do {
            val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
            try {
                // The generated tracks fixtures contain saturated test bars across the 16:9 frame.
                // Sample both upper and lower frame bands to reject stale/cropped Surface buffers.
                val videoWidth = min(bitmap.width.toFloat(), bitmap.height * 16f / 9f)
                val videoHeight = videoWidth * 9f / 16f
                val left = (bitmap.width - videoWidth) / 2f
                val top = (bitmap.height - videoHeight) / 2f
                valid = listOf(0.2f, 0.8f).all { y ->
                    (2..8).count { x ->
                        val pixel = bitmap.getPixel((left + videoWidth * x / 10f).toInt(), (top + videoHeight * y).toInt())
                        val channels = listOf(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
                        channels.max() - channels.min() > 80
                    } >= 4
                }
            } finally { bitmap.recycle() }
            if (!valid) SystemClock.sleep(100)
        } while (!valid && SystemClock.uptimeMillis() < deadline)
        if (!valid) capture("product-paused-rotation-failure.png")
        assertTrue("Paused rotation must redraw the fixture into the centered video bounds", valid)
        // Compare the actual displayed frame; the 500-ms UI ticker can report a
        // pre-pause audio clock until a redraw publishes the final video PTS.
        val difference = expected.pixels.zip(nativeFramePixels()).map { (before, after) ->
            (abs(Color.red(before) - Color.red(after)) + abs(Color.green(before) - Color.green(after)) +
                abs(Color.blue(before) - Color.blue(after))) / 3.0
        }.average()
        assertTrue("Paused rotation must preserve the visible frame (difference=$difference)", difference < 12.0)
        onMain {
            assertTrue(!vm.playerState.value.playWhenReady)
            assertEquals(expected.identity, vm.playerState.value.playbackIdentity)
        }
    }

    private fun await(description: String, predicate: () -> Boolean) {
        // Pump Compose frames while asynchronous SAF registration updates the app effect.
        try {
            activity.waitUntil(20_000L) { onMain(predicate) }
            return
        } catch (_: ComposeTimeoutException) { }
        val details = onMain {
            val vm = ViewModelProvider(activity.activity)[PlaybackControllerViewModel::class.java]
            val selection = ViewModelProvider(activity.activity)[MediaSelectionViewModel::class.java]
            "state=${vm.playerState.value}\npending=${selection.pendingPlayback.value}\nnotice=${selection.notice.value}"
        }
        File(app.cacheDir, "product-ui-failure.txt").writeText(details)
        capture("product-ui-failure.png")
        error("Timed out: $description; $details")
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
