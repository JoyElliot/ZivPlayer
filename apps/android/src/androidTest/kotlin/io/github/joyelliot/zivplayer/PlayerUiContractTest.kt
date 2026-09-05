// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.designsystem.ZivPrimaryButton
import io.github.joyelliot.zivplayer.designsystem.ZivSlider
import io.github.joyelliot.zivplayer.designsystem.ZivStatusText
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.player.PlayerConnectionStatus
import io.github.joyelliot.zivplayer.feature.player.PlayerHomeScreen
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

    @Test
    fun playerScreenExposesEnabledTransportAndUnknownDurationResume() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val play = context.getString(PlayerR.string.player_play)
        val resume = context.getString(PlayerR.string.player_recent_resume, "0:42")
        var playClicks = 0
        composeRule.setContent {
            ZivTheme {
                PlayerHomeScreen(
                    state = PlayerUiState(
                        connectionStatus = PlayerConnectionStatus.CONNECTED,
                        playbackStatus = PlayerPlaybackStatus.PAUSED,
                        mediaId = "media",
                        title = "Example video",
                        positionMs = 42_000L,
                        canPlayPause = true,
                    ),
                    recentMedia = listOf(
                        RecentMediaUiItem(
                            mediaId = "recent",
                            title = "Recent stream",
                            positionMs = 42_000L,
                            durationMs = null,
                        ),
                    ),
                    onPlayPause = { ++playClicks },
                )
            }
        }

        composeRule.onNodeWithText("Example video").assertIsDisplayed()
        composeRule.onNodeWithText(play)
            .performScrollTo()
            .assertIsEnabled()
            .assertHasClickAction()
            .performClick()
        composeRule.onNodeWithText(resume).performScrollTo().assertIsDisplayed()
        composeRule.runOnIdle { assertEquals(1, playClicks) }
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
