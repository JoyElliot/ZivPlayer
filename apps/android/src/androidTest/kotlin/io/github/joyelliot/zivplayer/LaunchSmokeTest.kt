// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.feature.player.R as PlayerR
import org.junit.Rule
import org.junit.Test

class LaunchSmokeTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun appShellShowsOpenMediaAction() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val action = context.getString(PlayerR.string.player_open_media)

        // The transport's Open action precedes the per-history-entry Open actions.
        composeRule.onAllNodesWithText(action).onFirst().assertIsDisplayed().assertHasClickAction().assertIsEnabled()
    }
}
