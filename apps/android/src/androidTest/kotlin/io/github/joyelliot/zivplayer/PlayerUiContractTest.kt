// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasProgressBarRangeInfo
import androidx.compose.ui.test.hasStateDescription
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Density
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.designsystem.ZivPrimaryButton
import io.github.joyelliot.zivplayer.designsystem.ZivSlider
import io.github.joyelliot.zivplayer.designsystem.ZivStatusText
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.player.PlayerConnectionStatus
import io.github.joyelliot.zivplayer.feature.player.FullscreenPlayerScreen
import io.github.joyelliot.zivplayer.feature.player.RecentMediaScreen
import io.github.joyelliot.zivplayer.feature.player.PlayerPlaybackStatus
import io.github.joyelliot.zivplayer.feature.player.PlayerUiState
import io.github.joyelliot.zivplayer.feature.player.RecentMediaUiItem
import io.github.joyelliot.zivplayer.feature.player.R as PlayerR
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class PlayerUiContractTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1)
    val composeRule = createComposeRule()

    @Test fun miniPlayerNavigatesBackWithoutChangingPlaybackAndItsButtonDoesNotNavigate() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        var playbackClicks = 0
        composeRule.setContent {
            ZivPlayerApp(playerState = PlayerUiState(
                connectionStatus = PlayerConnectionStatus.CONNECTED, playbackStatus = PlayerPlaybackStatus.PAUSED,
                mediaId = "fixture", title = "Navigation fixture", hasVideo = true, canPlayPause = true,
            ), onPlayPause = { playbackClicks++ })
        }
        val video = context.getString(PlayerR.string.player_video_area)
        composeRule.onNodeWithText("Navigation fixture").performClick()
        composeRule.onNodeWithContentDescription(video).assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(io.github.joyelliot.zivplayer.feature.library.R.string.library_back_folders)).performClick()
        composeRule.onNodeWithContentDescription(video).assertDoesNotExist()
        composeRule.onNodeWithText("Navigation fixture").assertIsDisplayed()
        composeRule.runOnIdle { assertEquals(0, playbackClicks) }
        composeRule.onNodeWithContentDescription(context.getString(PlayerR.string.player_play)).performClick()
        composeRule.onNodeWithContentDescription(video).assertDoesNotExist()
        composeRule.runOnIdle { assertEquals(1, playbackClicks) }
    }

    @Test
    fun immersivePlayerExposesEnabledTransportWithoutTheOldForm() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val play = context.getString(PlayerR.string.player_play)
        var playClicks = 0
        composeRule.setContent {
            ZivTheme {
                FullscreenPlayerScreen(
                    state = PlayerUiState(
                        connectionStatus = PlayerConnectionStatus.CONNECTED,
                        playbackStatus = PlayerPlaybackStatus.PAUSED,
                        mediaId = "media",
                        title = "Example video",
                        positionMs = 42_000L,
                        canPlayPause = true,
                        hasVideo = true,
                    ),
                    videoContent = {}, onExit = {}, onSeekTo = {}, onPlaybackSpeedChange = {},
                    onVolumeChange = {}, onRepeatModeChange = {}, onSelectTrack = { _, _ -> },
                    onOpenSubtitle = {}, onBeginTemporarySpeed = { null },
                    onTemporarySpeedChange = { _, _ -> }, onEndTemporarySpeed = {},
                    onPlayPause = { ++playClicks },
                )
            }
        }

        composeRule.onNodeWithText("Example video").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(play)
            .assertIsEnabled()
            .assertHasClickAction()
            .performClick()
        composeRule.runOnIdle { assertEquals(1, playClicks) }
    }

    @Test fun recentPageRetainsResumeWithUnknownDuration() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        var opened: String? = null
        composeRule.setContent { ZivTheme {
            RecentMediaScreen(listOf(RecentMediaUiItem("recent", "Recent stream", positionMs = 42_000L)),
                onBack = {}, onOpen = { opened = it }, onForget = {})
        } }
        composeRule.onNodeWithText(context.getString(PlayerR.string.player_recent_resume, "0:42")).assertIsDisplayed()
        composeRule.onNodeWithText("Recent stream").performClick()
        composeRule.runOnIdle { assertEquals("recent", opened) }
    }

    @Test fun transportRemainsVisibleAcrossTheCompactWidthBoundary() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val width = mutableStateOf(480.dp)
        composeRule.setContent {
            CompositionLocalProvider(LocalDensity provides Density(1f)) {
                FullscreenPlayerScreen(
                    state = PlayerUiState(connectionStatus = PlayerConnectionStatus.CONNECTED,
                        playbackStatus = PlayerPlaybackStatus.PAUSED, mediaId = "width-fixture",
                        title = "Width fixture", hasVideo = true, canPlayPause = true),
                    videoContent = {}, onExit = {}, onPlayPause = {}, onSeekTo = {}, onPlaybackSpeedChange = {},
                    onVolumeChange = {}, onRepeatModeChange = {}, onSelectTrack = { _, _ -> }, onOpenSubtitle = {},
                    onBeginTemporarySpeed = { null }, onTemporarySpeedChange = { _, _ -> }, onEndTemporarySpeed = {},
                    modifier = Modifier.requiredSize(width.value, 400.dp),
                )
            }
        }
        for (testWidth in listOf(480.dp, 500.dp, 520.dp, 600.dp)) {
            composeRule.runOnIdle { width.value = testWidth }
            composeRule.onNodeWithContentDescription(context.getString(PlayerR.string.player_play)).assertIsDisplayed().assertIsEnabled()
            composeRule.onNodeWithContentDescription(context.getString(PlayerR.string.player_previous)).assertIsDisplayed()
            composeRule.onNodeWithContentDescription(context.getString(PlayerR.string.player_next)).assertIsDisplayed()
        }
    }

    @Test
    fun wrappersExposeDisabledStateRangeLiveRegionAndMinimumTouchHeight() {
        var changedVolume: Float? = null
        composeRule.setContent {
            ZivTheme {
                Column {
                    ZivPrimaryButton(
                        text = "Disabled action",
                        onClick = {},
                        enabled = false,
                    )
                    ZivSlider(
                        value = 0.25f,
                        onValueChange = { changedVolume = it },
                        stateDescription = "0:15 / 1:00",
                    )
                    ZivSlider(
                        value = 0.5f,
                        onValueChange = {},
                        enabled = false,
                        stateDescription = "Disabled range",
                    )
                    ZivStatusText(text = "Polite status")
                }
            }
        }

        composeRule.onNodeWithText("Disabled action")
            .assertIsNotEnabled()
            .assertHasClickAction()
            .assertHeightIsAtLeast(48.dp)
        val slider = composeRule.onNode(
            hasProgressBarRangeInfo(ProgressBarRangeInfo(0.25f, 0f..1f, 0)),
        )
        slider.assert(hasStateDescription("0:15 / 1:00"))
            .assertHeightIsAtLeast(48.dp)
        slider.performSemanticsAction(SemanticsActions.SetProgress) { setProgress ->
            assertTrue(setProgress(0.75f))
        }
        composeRule.runOnIdle { assertEquals(0.75f, changedVolume ?: 0f, 0f) }
        composeRule.onNode(hasStateDescription("Disabled range")).assertIsNotEnabled()
        composeRule.onNodeWithText("Polite status").assert(
            SemanticsMatcher.expectValue(
                SemanticsProperties.LiveRegion,
                LiveRegionMode.Polite,
            ),
        )
    }
}
