// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem.liquid

import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

internal data class LiquidNavigationFrame(
    val offsetPx: Float = 0f,
    val panelScale: Float = 1f,
)

/** Read the navigation's actual spring values from graphics layers, not during composition. */
@Stable
internal class LiquidNavigationMotion {
    var source: () -> LiquidNavigationFrame by mutableStateOf(Idle)

    companion object {
        private val restingFrame = LiquidNavigationFrame()
        val Idle: () -> LiquidNavigationFrame = { restingFrame }
    }
}
