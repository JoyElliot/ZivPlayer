// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.runtime.Composable
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import io.github.joyelliot.zivplayer.feature.player.PlayerHomeScreen

@Composable
fun ZivPlayerApp() {
    ZivTheme {
        PlayerHomeScreen()
    }
}
