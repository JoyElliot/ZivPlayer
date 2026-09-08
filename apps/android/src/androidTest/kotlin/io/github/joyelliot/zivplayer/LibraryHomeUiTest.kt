// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.runtime.mutableStateOf
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.media.*
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.library.LibraryScreen
import io.github.joyelliot.zivplayer.feature.library.R as LibraryR
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** In-memory library fixtures never request SAF access or modify the device index. */
class LibraryHomeUiTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val roots = listOf(LibraryFolder("root", DurableMediaUri("content://test/tree/root"), "Storage", LibraryFolderStatus.READY, null, 4))
    private val files = listOf(media("Movies/A.mp4"), media("Movies/B.mp4"), media("Movies/Private.mp4", true), media("Music/Song.flac"))
    private var queue: List<LibraryMedia>? = null
    private var index = -1
    private var browseBack: (() -> Unit)? = null

    @Test fun directoriesShowVisibleCountsAndOpenOnlyTheirOwnOrderedFiles() {
        showLibrary()
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Movies").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Movies").assertIsDisplayed()
        compose.onNodeWithText("Music").assertIsDisplayed()
        compose.onNodeWithText("Private.mp4").assertDoesNotExist()
        compose.onNodeWithText("Movies").performClick()
        compose.waitUntil(5_000) { compose.onAllNodesWithText("B.mp4").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Private.mp4").assertDoesNotExist()
        compose.onNodeWithText("Song.flac").assertDoesNotExist()
        compose.onNodeWithText("B.mp4").performClick()
        compose.runOnIdle { assertEquals(listOf(files[0], files[1]), queue); assertEquals(1, index) }
    }

    @Test fun searchAndPlayAllUseTheVisibleSnapshot() {
        showLibrary()
        compose.onNodeWithText(context.getString(LibraryR.string.library_all)).performClick()
        compose.onNodeWithContentDescription(context.getString(LibraryR.string.library_search)).performClick()
        compose.onNode(hasSetTextAction()).performTextInput("B.mp4")
        compose.waitUntil(5_000) {
            compose.onAllNodesWithText("B.mp4").fetchSemanticsNodes().isNotEmpty() &&
                compose.onAllNodesWithText("A.mp4").fetchSemanticsNodes().isEmpty()
        }
        compose.onNodeWithContentDescription(context.getString(LibraryR.string.library_play_all)).performClick()
        compose.runOnIdle { assertEquals(listOf(files[1]), queue); assertEquals(0, index) }
    }

    @Test fun backClosesSearchWithoutDiscardingTheAudioFilter() {
        showLibrary()
        compose.onNodeWithText(context.getString(LibraryR.string.library_audio)).performClick()
        compose.onNodeWithContentDescription(context.getString(LibraryR.string.library_search)).performClick()
        compose.onNode(hasSetTextAction()).performTextInput("no-match")
        compose.runOnIdle { checkNotNull(browseBack).invoke() }
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Song.flac").fetchSemanticsNodes().isNotEmpty() }
        compose.onNode(hasSetTextAction()).assertDoesNotExist()
        compose.onNodeWithText("A.mp4").assertDoesNotExist()
    }

    @Test fun hidingTheLastVisibleFileRetainsTheSourceWithoutShowingHiddenNames() {
        val visible = mutableStateOf(listOf(files[0]))
        compose.setContent { ZivTheme {
            LibraryScreen(roots, visible.value, null, 0, null, {}, {}, {}, {}, {}, {}, {})
        } }
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Movies").fetchSemanticsNodes().isNotEmpty() }
        compose.runOnIdle { visible.value = listOf(files[0].copy(hidden = true)) }
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Storage").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Movies").assertDoesNotExist()
        compose.onNodeWithText("A.mp4").assertDoesNotExist()
    }

    @Test fun singleFileInLargeDirectoryStillPlaysWithoutTruncatingPlayAll() {
        val large = (0..500).map { media("Large/Clip${it.toString().padStart(3, '0')}.mp4") }
        var opened: LibraryMedia? = null
        compose.setContent { ZivTheme {
            LibraryScreen(roots, large, null, 0, null, {}, {}, {}, {}, { opened = it }, {}, {},
                onOpenQueue = { items, start -> queue = items; index = start })
        } }
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Large").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Large").performClick()
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Clip000.mp4").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Clip000.mp4").performClick()
        compose.runOnIdle { assertEquals(large[0], opened); assertEquals(null, queue) }
        compose.onNodeWithContentDescription(context.getString(LibraryR.string.library_play_all)).performClick()
        compose.runOnIdle { assertEquals(large, queue); assertEquals(0, index) }
    }

    @Test fun selectedDirectorySurvivesPendingIndexButReturnsHomeAfterRemoval() {
        val availableRoots = mutableStateOf(roots)
        val loaded = mutableStateOf(true)
        compose.setContent { ZivTheme {
            LibraryScreen(availableRoots.value, files, null, 0, null, {}, {}, {}, {}, {}, {}, {},
                foldersLoaded = loaded.value)
        } }
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Movies").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Movies").performClick()
        compose.waitUntil(5_000) { compose.onAllNodesWithText("A.mp4").fetchSemanticsNodes().isNotEmpty() }
        compose.runOnIdle { loaded.value = false; availableRoots.value = emptyList() }
        compose.waitForIdle()
        compose.runOnIdle { availableRoots.value = roots; loaded.value = true }
        compose.waitUntil(5_000) { compose.onAllNodesWithText("A.mp4").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Song.flac").assertDoesNotExist()
        compose.onNodeWithContentDescription(context.getString(LibraryR.string.library_back_folders)).assertIsDisplayed()
        compose.runOnIdle { availableRoots.value = emptyList() }
        compose.waitUntil(5_000) {
            compose.onAllNodesWithContentDescription(context.getString(LibraryR.string.library_back_folders)).fetchSemanticsNodes().isEmpty()
        }
        compose.waitUntil(5_000) {
            compose.onAllNodesWithText(context.getString(LibraryR.string.library_add_folder)).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText(context.getString(LibraryR.string.library_add_folder)).assertIsDisplayed()
    }

    private fun showLibrary() = compose.setContent { ZivTheme {
        LibraryScreen(roots, files, null, 0, null, {}, {}, {}, {}, {}, {}, {},
            onOpenQueue = { items, start -> queue = items; index = start }, onBrowseBackChanged = { browseBack = it })
    } }

    private fun media(path: String, hidden: Boolean = false) = LibraryMedia("root", DurableMediaUri("content://test/$path"),
        path.substringAfterLast('/'), if (path.endsWith("flac")) "audio/flac" else "video/mp4", path, 1024, null, hidden = hidden)
}
