// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf

/** A host can keep page/dock geometry in its own coordinate system during window rotation. */
data class ZivInsetSources(val safeDrawing: WindowInsets, val barsAndCutout: WindowInsets,
    val navigationBars: WindowInsets, val ime: WindowInsets)

val LocalZivInsetSources = staticCompositionLocalOf<ZivInsetSources?> { null }

@OptIn(ExperimentalLayoutApi::class)
object ZivInsets {
    val safeDrawing: WindowInsets @Composable get() = LocalZivInsetSources.current?.safeDrawing ?: WindowInsets.safeDrawing
    val barsAndCutout: WindowInsets @Composable get() = LocalZivInsetSources.current?.barsAndCutout ?:
        WindowInsets.systemBarsIgnoringVisibility.union(WindowInsets.displayCutout)
    val navigationBars: WindowInsets @Composable get() = LocalZivInsetSources.current?.navigationBars ?: WindowInsets.navigationBarsIgnoringVisibility
    val ime: WindowInsets @Composable get() = LocalZivInsetSources.current?.ime ?: WindowInsets.ime
}
