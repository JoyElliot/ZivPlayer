// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.feature.library.R as LibraryR
import org.junit.Rule
import org.junit.Test

class LaunchSmokeTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1)
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun appShellShowsOpenMediaAction() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val action = context.getString(LibraryR.string.library_open_file)

        // Document opening is available from the folder toolbar, without a separate player tab.
        composeRule.onNodeWithContentDescription(context.getString(LibraryR.string.library_item_actions)).performClick()
        composeRule.onAllNodesWithText(action).onFirst().assertIsDisplayed().assertHasClickAction().assertIsEnabled()
    }
}
