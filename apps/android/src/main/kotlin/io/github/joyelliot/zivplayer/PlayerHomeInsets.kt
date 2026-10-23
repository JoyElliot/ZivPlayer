// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.os.Build
import android.view.WindowInsets.Type
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import io.github.joyelliot.zivplayer.designsystem.ZivInsetSources

/** Preserve the entry home layout even when system insets arrive ahead of the rotated window. */
internal class PlayerHomeInsetLatch {
    private var entry: PlayerSafeArea? = null
    fun resolve(source: PlayerSafeArea, ready: Boolean, hold: Boolean, turns: Int): PlayerSafeArea {
        val logical = source.turned(-turns)
        if (ready && (!hold || entry == null)) entry = logical
        return entry ?: logical
    }
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
internal fun rememberPlayerHomeInsets(basis: PlayerRotationBasis?, canvas: PlayerCanvasGeometry): ZivInsetSources {
    val bars = WindowInsets.systemBarsIgnoringVisibility.union(WindowInsets.displayCutout)
    val navigation = WindowInsets.navigationBarsIgnoringVisibility
    val ime = WindowInsets.ime
    val density = LocalDensity.current
    val view = LocalView.current
    val barsLatch = remember(canvas.width, canvas.height, density) { PlayerHomeInsetLatch() }
    val navLatch = remember(canvas.width, canvas.height, density) { PlayerHomeInsetLatch() }
    return remember(basis, canvas, view, bars, navigation, ime, barsLatch, navLatch) {
        fun stable(source: WindowInsets, types: Int, latch: PlayerHomeInsetLatch) = object : WindowInsets {
            fun area(density: Density): PlayerSafeArea {
                val observed = PlayerSafeArea(source.getLeft(density, LayoutDirection.Ltr), source.getTop(density),
                    source.getRight(density, LayoutDirection.Ltr), source.getBottom(density))
                val snapshot = view.rootWindowInsets
                val frame = if (Build.VERSION.SDK_INT >= 35) snapshot?.frame else null
                val ready = snapshot != null && (frame == null || frame.width == canvas.windowWidth && frame.height == canvas.windowHeight)
                val native = snapshot?.getInsetsIgnoringVisibility(types)
                return latch.resolve(native?.let { PlayerSafeArea(it.left, it.top, it.right, it.bottom) } ?: observed,
                    ready, basis != null, canvas.turns)
            }
            override fun getLeft(density: Density, layoutDirection: LayoutDirection) = area(density).left
            override fun getTop(density: Density) = area(density).top
            override fun getRight(density: Density, layoutDirection: LayoutDirection) = area(density).right
            override fun getBottom(density: Density) = area(density).bottom
        }
        val stableBars = stable(bars, Type.systemBars() or Type.displayCutout(), barsLatch)
        val stableNavigation = stable(navigation, Type.navigationBars(), navLatch)
        val keyboard = if (basis != null) WindowInsets(0) else ime
        ZivInsetSources(stableBars.union(keyboard), stableBars, stableNavigation, keyboard)
    }
}
