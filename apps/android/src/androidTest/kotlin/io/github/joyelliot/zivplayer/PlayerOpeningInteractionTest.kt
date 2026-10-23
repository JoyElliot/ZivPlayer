// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.activity.ComponentActivity
import androidx.activity.compose.LocalActivity
import androidx.compose.runtime.*
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.junit4.StateRestorationTester
import io.github.joyelliot.zivplayer.feature.player.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Real app state transitions with window acknowledgement deliberately controlled by the test. */
class PlayerOpeningInteractionTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()

    private fun fixture(aspect: Float? = 16f / 9) = PlayerUiState(
        mediaId = "opening", playbackIdentity = "opening", sourceUri = "fixture:opening",
        title = "Opening fixture", hasVideo = true, orientationAspectRatio = aspect,
        playbackStatus = if (aspect == null) PlayerPlaybackStatus.LOADING else PlayerPlaybackStatus.PAUSED)

    @Test fun lateGeometryChoosesLandscapeBeforeRequestingTheWindow() {
        var state by mutableStateOf(fixture(null))
        var turned by mutableStateOf(false)
        val requests = mutableListOf<Boolean>()
        compose.setContent {
            ZivPlayerApp(playerState = state, fullscreen = turned,
                rotationBasis = PlayerRotationBasis(0, false), onFullscreenChange = { turned = it },
                onRotationAnimationFinished = { turn, _ -> requests += turn })
        }
        compose.waitForIdle()
        compose.runOnIdle { requests.clear() }
        compose.mainClock.autoAdvance = false
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.mainClock.advanceTimeBy(500)
        compose.runOnIdle {
            assertFalse(turned); assertTrue(requests.isEmpty())
            state = state.copy(playbackStatus = PlayerPlaybackStatus.PAUSED)
        }
        compose.mainClock.advanceTimeBy(500)
        compose.runOnIdle { assertFalse(turned); assertTrue(requests.isEmpty()); state = fixture() }
        compose.mainClock.advanceTimeBy(160)
        compose.runOnIdle { assertTrue(turned); assertTrue(requests.isEmpty()) }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(listOf(true), requests) }
    }

    @Test fun waitingOpeningRestoresAtMiniAndStillHandsOffOnceDimensionsArrive() {
        val restoration = StateRestorationTester(compose)
        var state by mutableStateOf(fixture(null))
        var turned by mutableStateOf(false)
        val requests = mutableListOf<Boolean>()
        restoration.setContent {
            ZivPlayerApp(playerState = state, fullscreen = turned,
                rotationBasis = PlayerRotationBasis(0, false), onFullscreenChange = { turned = it },
                onRotationAnimationFinished = { turn, _ -> requests += turn })
        }
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.waitForIdle()
        compose.runOnIdle { requests.clear() }
        restoration.emulateSavedInstanceStateRestore()
        compose.runOnIdle { state = fixture() }
        compose.waitForIdle()
        compose.runOnIdle { assertTrue(turned); assertEquals(listOf(true), requests) }
    }

    @Test fun audioDiscoveryUnblocksOpeningWithoutAStatusOrIdentityChange() {
        var state by mutableStateOf(fixture(null).copy(hasVideo = false, playbackStatus = PlayerPlaybackStatus.PAUSED))
        var turned by mutableStateOf(false)
        val requests = mutableListOf<Boolean>()
        compose.setContent {
            ZivPlayerApp(playerState = state, fullscreen = turned,
                rotationBasis = PlayerRotationBasis(0, false), onFullscreenChange = { turned = it },
                onRotationAnimationFinished = { turn, _ -> requests += turn })
        }
        compose.waitForIdle()
        compose.runOnIdle { requests.clear() }
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.waitForIdle()
        compose.runOnIdle {
            assertTrue(requests.isEmpty())
            state = state.copy(tracks = listOf(PlayerTrackUiItem("audio", PlayerTrackKind.AUDIO, "Audio", true)))
        }
        compose.waitForIdle()
        compose.runOnIdle { assertFalse(turned); assertEquals(listOf(false), requests) }
    }

    @Test fun manualPortraitSurvivesAMiniRoundTripForTheSamePlayback() {
        lateinit var host: ComponentActivity
        lateinit var rotate: String
        lateinit var play: String
        var turned by mutableStateOf(false)
        val requests = mutableListOf<Boolean>()
        compose.setContent {
            host = LocalActivity.current as ComponentActivity
            rotate = stringResource(io.github.joyelliot.zivplayer.feature.player.R.string.player_rotate)
            play = stringResource(io.github.joyelliot.zivplayer.feature.player.R.string.player_play)
            ZivPlayerApp(playerState = fixture(), fullscreen = turned,
                rotationBasis = PlayerRotationBasis(0, false), onFullscreenChange = { turned = it },
                onRotationAnimationFinished = { turn, _ -> requests += turn })
        }
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.waitForIdle()
        compose.runOnIdle { assertTrue(turned) }
        val playNode = compose.onNodeWithContentDescription(play).fetchSemanticsNode().id
        compose.onNodeWithContentDescription(rotate).performClick()
        compose.waitForIdle()
        assertEquals("Transport controls retain their nodes when the viewport changes axes",
            playNode, compose.onNodeWithContentDescription(play).fetchSemanticsNode().id)
        compose.runOnIdle { assertFalse(turned); host.onBackPressedDispatcher.onBackPressed() }
        compose.waitForIdle()
        compose.runOnIdle { requests.clear() }
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.waitForIdle()
        compose.runOnIdle { assertFalse(turned); assertTrue(requests.none { it }) }
    }

    @Test fun backgroundCancellationDoesNotLeaveAWaitingPlayerOverHome() {
        lateinit var host: ComponentActivity
        var active by mutableStateOf(true)
        var state by mutableStateOf(fixture(null))
        var turned by mutableStateOf(false)
        compose.setContent {
            host = LocalActivity.current as ComponentActivity
            ZivPlayerApp(playerState = state, fullscreen = turned, rotationActive = active,
                rotationBasis = PlayerRotationBasis(0, false), onFullscreenChange = { turned = it })
        }
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.runOnIdle { active = false }
        compose.waitForIdle()
        compose.runOnIdle { active = true; state = fixture() }
        compose.waitForIdle()
        compose.onNodeWithTag("app_queue").performClick()
        compose.onNodeWithTag("app_queue").assertIsSelected()
        compose.runOnIdle { assertFalse(turned); host.onBackPressedDispatcher.onBackPressed() }
        compose.waitForIdle()
        compose.onNodeWithTag("mini_player", true).performClick()
        compose.waitForIdle()
        compose.runOnIdle { assertTrue(turned) }
    }
}
