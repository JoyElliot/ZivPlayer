// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.media.*
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.library.LibraryScreen
import io.github.joyelliot.zivplayer.feature.library.R as LibraryR
import io.github.joyelliot.zivplayer.feature.player.*
import kotlinx.coroutines.runBlocking
import org.junit.Rule
import org.junit.Test
import java.io.File

class MediaArtworkUiTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val store get() = (context.applicationContext as ZivPlayerApplication).mediaDetailsRepository
    private val uri = DurableMediaUri("content://io.github.joyelliot.zivplayer.test.fixtures/metadata-music.mp3")

    @Test fun libraryShowsExtractedArtworkAndOpensMetadataInformation() {
        val item = LibraryMedia("metadata-fixture", uri, "metadata-music.mp3", "audio/mpeg", "metadata-music.mp3", null, null, false, false)
        compose.setContent { ZivTheme {
            val details = store.details.collectAsState().value
            LibraryScreen(emptyList(), listOf(item), null, 0, null, {}, {}, {}, {}, {}, {}, {},
                mediaDetails = details, onLoadDetails = { store.load(MediaDetailsRequest(it.sourceUri, it.title, it.mimeType)) })
        } }
        compose.onNodeWithText(context.getString(LibraryR.string.library_audio)).performClick()
        compose.waitUntil(10_000) { compose.onAllNodesWithText("Evening Test").fetchSemanticsNodes().isNotEmpty() &&
            compose.onAllNodesWithTag("media-artwork-loaded", useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("ZivPlayer Artist", substring = true).assertIsDisplayed()
        screenshot("metadata-library.png")
        compose.onNodeWithContentDescription(context.getString(LibraryR.string.library_file_actions, item.title)).performClick()
        compose.onNodeWithText(context.getString(LibraryR.string.library_details)).performClick()
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Original Test Album").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Original Test Album").assertIsDisplayed()
        screenshot("metadata-information.png")
    }

    @Test fun musicPlayerDisplaysRealAlbumArtworkAndArtist() {
        val details = runBlocking { checkNotNull(store.load(MediaDetailsRequest(uri))) }
        val state = PlayerUiState(connectionStatus = PlayerConnectionStatus.CONNECTED,
            playbackStatus = PlayerPlaybackStatus.PAUSED, mediaId = "metadata-ui", title = details.title,
            supportingText = "${details.artist} · ${details.album}", artworkLocator = details.artworkLocator,
            durationMs = details.durationMs, canPlayPause = true, canSeek = true, hasVideo = false)
        compose.setContent { FullscreenPlayerScreen(state, { Box {} }, {}, {}, {}, {}, {}, {}, { _, _ -> }, {},
            { null }, { _, _ -> }, {}) }
        compose.waitUntil(8_000) { compose.onAllNodesWithTag("media-artwork-loaded", useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("${details.artist} · ${details.album}").assertIsDisplayed()
        screenshot("metadata-music-player.png")
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        android.os.SystemClock.sleep(500) // Also let the platform dialog-window animation settle.
        val image = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        try { File(context.cacheDir, name).outputStream().use { check(image.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { image.recycle() }
    }
}
