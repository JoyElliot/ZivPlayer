// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.feature.player.FullscreenPlayerScreen
import io.github.joyelliot.zivplayer.feature.player.PlayerConnectionStatus
import io.github.joyelliot.zivplayer.feature.player.PlayerPlaybackStatus
import io.github.joyelliot.zivplayer.feature.player.PlayerUiState
import io.github.joyelliot.zivplayer.feature.player.R as PlayerR
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Injects actual Compose pointer streams; callback assertions distinguish seeking from holding. */
class FullscreenGestureTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val state = mutableStateOf(PlayerUiState(connectionStatus = PlayerConnectionStatus.CONNECTED,
        playbackStatus = PlayerPlaybackStatus.PLAYING, mediaId = "gesture-media", playbackIdentity = "occurrence-1",
        title = "Gesture video", positionMs = 100_000L, durationMs = 400_000L, playWhenReady = true,
        canPlayPause = true, canSeek = true, canSetSpeed = true))
    private var playPause = 0
    private val seeks = mutableListOf<Long>()
    private val rates = mutableListOf<Float>()
    private val ended = mutableListOf<Long>()
    private var token = 0L

    @Test fun singleTapTogglesControlsAndDoubleTapOnlyTogglesPlayback() {
        showPlayer()
        video().performTouchInput { click(center) }
        compose.mainClock.advanceTimeBy(400)
        compose.onNodeWithText("Gesture video").assertDoesNotExist()
        video().performTouchInput { doubleClick(center) }
        compose.mainClock.advanceTimeBy(400)
        compose.onNodeWithText("Gesture video").assertDoesNotExist()
        compose.runOnIdle { assertEquals(1, playPause); assertTrue(seeks.isEmpty()); assertTrue(rates.isEmpty()) }
    }

    @Test fun horizontalSwipeCommitsOneBoundedSeek() {
        showPlayer()
        video().performTouchInput { swipe(Offset(width * 0.2f, height * 0.45f), Offset(width * 0.7f, height * 0.45f), 300) }
        compose.runOnIdle {
            assertEquals(1, seeks.size)
            assertTrue(seeks.single() in 155_000L..165_000L)
            assertEquals(0, playPause)
            assertTrue(rates.isEmpty())
        }
    }

    @Test fun holdSlidesIntoSlowMotionAndReleaseOrIdentityChangeEndsLease() {
        showPlayer()
        video().performTouchInput { down(Offset(width * 0.7f, height * 0.45f)) }
        compose.mainClock.advanceTimeBy(650)
        compose.runOnIdle { assertEquals(2f, rates.last(), 0f) }
        video().performTouchInput { moveTo(Offset(width * 0.05f, height * 0.45f)) }
        compose.runOnIdle { assertEquals(0.25f, rates.last(), 0f) }
        video().performTouchInput { up() }
        compose.runOnIdle { assertEquals(listOf(1L), ended); assertTrue(seeks.isEmpty()) }
        video().performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(650)
        compose.runOnIdle { state.value = state.value.copy(playbackIdentity = "occurrence-2") }
        compose.mainClock.advanceTimeByFrame()
        compose.runOnIdle { assertEquals(listOf(1L, 2L), ended) }
        video().performTouchInput { up() }
        compose.runOnIdle { assertEquals(0, playPause) }
    }

    @Test fun quickMenuControlsConsumeTouchesAndDismiss() {
        showPlayer()
        compose.onNodeWithContentDescription(context.getString(PlayerR.string.player_quick_menu)).performTouchInput { click() }
        compose.mainClock.advanceTimeByFrame()
        compose.mainClock.advanceTimeByFrame()
        compose.onNodeWithContentDescription(context.getString(PlayerR.string.player_close_menu)).assertIsDisplayed().performTouchInput { click() }
        compose.mainClock.advanceTimeBy(400)
        compose.onNodeWithContentDescription(context.getString(PlayerR.string.player_close_menu)).assertDoesNotExist()
        compose.runOnIdle { assertEquals(0, playPause); assertTrue(seeks.isEmpty()); assertTrue(rates.isEmpty()) }
    }

    @Test fun timelineCommitsOnceAndCancelKeepsPosition() {
        showPlayer()
        val timeline = compose.onNode(SemanticsMatcher.keyIsDefined(SemanticsActions.SetProgress))
        timeline.performTouchInput { swipe(Offset(width * 0.3f, height / 2f), Offset(width * 0.8f, height / 2f), 300) }
        compose.runOnIdle { assertEquals(1, seeks.size); assertTrue(seeks.single() in 300_000L..340_000L) }
        timeline.performTouchInput { down(center); moveTo(Offset(width * 0.1f, height / 2f)); cancel() }
        compose.runOnIdle { assertEquals(1, seeks.size); assertEquals(0, playPause); assertTrue(rates.isEmpty()) }
        timeline.performTouchInput { down(center); moveTo(Offset(width * 0.6f, height / 2f)) }
        compose.runOnIdle { state.value = state.value.copy(playbackIdentity = "replacement", durationMs = 100_000L) }
        compose.mainClock.advanceTimeByFrame()
        compose.onNode(SemanticsMatcher.keyIsDefined(SemanticsActions.SetProgress)).performTouchInput { up() }
        compose.runOnIdle { assertEquals(1, seeks.size) }
    }

    @Test fun lockedVideoIgnoresPlaybackGesturesUntilUnlocked() {
        showPlayer()
        compose.onNodeWithContentDescription(context.getString(PlayerR.string.player_lock_controls)).performClick()
        compose.mainClock.advanceTimeByFrame()
        video().performTouchInput { doubleClick(center) }
        compose.mainClock.advanceTimeBy(400)
        compose.runOnIdle { assertEquals(0, playPause); assertTrue(seeks.isEmpty()); assertTrue(rates.isEmpty()) }
        compose.onNodeWithContentDescription(context.getString(PlayerR.string.player_unlock_controls)).performClick()
        compose.mainClock.advanceTimeByFrame()
        video().performTouchInput { doubleClick(center) }
        compose.mainClock.advanceTimeBy(400)
        compose.runOnIdle { assertEquals(1, playPause) }
    }

    private fun showPlayer() {
        compose.mainClock.autoAdvance = false
        compose.setContent {
            FullscreenPlayerScreen(state.value, videoContent = { Box {} }, onExit = {}, onPlayPause = { playPause++ },
                onSeekTo = { seeks += it }, onPlaybackSpeedChange = {}, onVolumeChange = {}, onRepeatModeChange = {},
                onSelectTrack = { _, _ -> }, onOpenSubtitle = {}, onBeginTemporarySpeed = { ++token },
                onTemporarySpeedChange = { _, rate -> rates += rate }, onEndTemporarySpeed = { ended += it })
        }
        compose.mainClock.advanceTimeByFrame()
    }

    private fun video() = compose.onNodeWithContentDescription(context.getString(PlayerR.string.player_video_area))
}
