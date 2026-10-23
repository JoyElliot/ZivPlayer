// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.activity.ComponentActivity
import androidx.activity.compose.LocalActivity
import androidx.compose.runtime.*
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import io.github.joyelliot.zivplayer.feature.player.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Exercises the actual app overlay and queue, with WM deliberately not changing orientation. */
class PlayerHomeInteractionTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()

    @Test fun exposedHomeAcceptsScrollAndBackBeforeTheCollapseRequestsSystemDirection() {
        lateinit var host: ComponentActivity
        var turned by mutableStateOf(false)
        val windowRequests = mutableListOf<Boolean>()
        val state = PlayerUiState(mediaId = "input-fixture", playbackIdentity = "input-fixture",
            sourceUri = "fixture:video", title = "Input fixture", hasVideo = true,
            orientationAspectRatio = 16f / 9, playbackStatus = PlayerPlaybackStatus.PAUSED,
            queue = List(80) { PlayerQueueUiItem(it, "Video $it", it == 0) })
        compose.setContent {
            host = LocalActivity.current as ComponentActivity
            ZivPlayerApp(playerState = state, fullscreen = turned,
                rotationBasis = PlayerRotationBasis(0, false), onFullscreenChange = { turned = it },
                onRotationAnimationFinished = { turn, _ -> windowRequests += turn })
        }
        compose.onNodeWithTag("app_queue").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("mini_player", useUnmergedTree = true).performClick()
        compose.waitForIdle()
        compose.runOnIdle { assertTrue(turned); windowRequests.clear() }
        compose.mainClock.autoAdvance = false
        compose.runOnIdle { host.onBackPressedDispatcher.onBackPressed() }
        compose.mainClock.advanceTimeBy(160)
        val list = compose.onNodeWithTag("playback_queue_list")
        val before = list.fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        list.performTouchInput {
            swipe(Offset(width * 0.2f, height * 0.45f), Offset(width * 0.2f, height * 0.1f), 80)
        }
        compose.mainClock.advanceTimeByFrame()
        val after = list.fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        compose.runOnIdle {
            assertTrue("The revealed queue must accept touch before the mini endpoint", after > before)
            assertTrue("No return-direction request should be needed to unblock the list", windowRequests.isEmpty())
            host.onBackPressedDispatcher.onBackPressed()
        }
        compose.mainClock.advanceTimeByFrame()
        compose.onNodeWithTag("app_video").assertIsSelected()
        compose.mainClock.advanceTimeBy(2_000)
    }
}
