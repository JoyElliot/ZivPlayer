// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.feature.player.R as PlayerR
import org.junit.Rule
import org.junit.Test

class LaunchSmokeTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun appShellShowsOpenMediaAction() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val action = context.getString(PlayerR.string.player_open_media)

        composeRule.onNodeWithText(action).assertIsDisplayed()
    }
}
