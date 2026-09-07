// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.graphics.Bitmap
import android.os.SystemClock
import android.view.WindowManager
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
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
        onMain { activity.activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
        try {
            activity.onAllNodesWithText(app.getString(R.string.app_library)).onLast().performClick()
            activity.onNodeWithText(folderName).performScrollTo().performClick()
            activity.onNode(hasSetTextAction()).performTextInput("tracks-")
            activity.waitUntil(5_000) { activity.onAllNodesWithText(expected.last().title).fetchSemanticsNodes().isNotEmpty() }
            capture("product-folder-filtered.png")
            activity.onNodeWithText(app.getString(LibraryR.string.library_play_all)).performScrollTo().performClick()
            await("folder queue playing") { vm.playerState.value.let { it.queue.size == 2 && it.playWhenReady && it.positionMs > 300L && it.title == expected.first().title } }
            await("fullscreen control enabled") { vm.playerState.value.let { it.canRenderVideo && it.hasVideo } }
            activity.onNodeWithText(app.getString(R.string.app_fullscreen)).performScrollTo().assertIsEnabled().performClick()
            await("fullscreen attached") { activity.activity.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE }
            SystemClock.sleep(750)
            val originalSpeed = onMain { vm.playerState.value.playbackSpeed }
            val video = activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_video_area))
            video.performTouchInput { down(Offset(width * 0.6f, height * 0.45f)) }
            await("actual service receives held 2x") { vm.controller.value?.playbackParameters?.speed == 2f }
            video.performTouchInput { moveTo(Offset(width * 0.05f, height * 0.45f)) }
            await("actual service receives held slow motion") { vm.controller.value?.playbackParameters?.speed == 0.25f }
            capture("product-fullscreen-slow-motion.png")
            video.performTouchInput { up() }
            await("actual release restores original speed") { vm.controller.value?.playbackParameters?.speed == originalSpeed }
            video.performTouchInput { doubleClick(center) }
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
            await("queue selection plays second") { vm.playerState.value.let { it.title == expected.last().title && it.playWhenReady } }
            activity.onNodeWithContentDescription(app.getString(PlayerR.string.player_exit_fullscreen)).performClick()
            capture("product-player-portrait.png")
        } finally { onMain { vm.cancelTemporarySpeed(); vm.stop() } }
    }

    private fun capture(name: String) {
        activity.waitForIdle()
        SystemClock.sleep(250)
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        try { File(app.cacheDir, name).outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
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
