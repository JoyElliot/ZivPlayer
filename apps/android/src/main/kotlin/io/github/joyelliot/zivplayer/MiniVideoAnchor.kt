// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onLayoutRectChanged
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.ZivNavigationStyle

internal val LocalPlayerCanvasCoordinates = staticCompositionLocalOf<LayoutCoordinates?> { null }

/** Both docks report their bounds relative to the persistent logical canvas, including glass motion. */
@Composable
internal fun MiniVideoAnchor(style: ZivNavigationStyle, onFrame: (VideoFrame) -> Unit) {
    val root = LocalPlayerCanvasCoordinates.current
    var slot by remember { mutableStateOf<LayoutCoordinates?>(null) }
    fun report() {
        val child = slot ?: return
        if (root?.isAttached != true || !child.isAttached) return
        val bounds = root.localBoundingBoxOf(child, clipBounds = false)
        if (bounds.width > 0 && bounds.height > 0) onFrame(VideoFrame(bounds.center.x, bounds.center.y, bounds.width, bounds.height))
    }
    SideEffect { report() }
    Box(Modifier.size(56.dp, if (style == ZivNavigationStyle.LIQUID_GLASS) 32.dp else 36.dp)
        .testTag("mini_video_anchor")
        .onGloballyPositioned { slot = it; report() }
        // Include ancestor graphics-layer translation/scale, not just measured layout changes.
        // No debounce: the video must follow the glass spring while the navigation is held.
        .onLayoutRectChanged(throttleMillis = 0, debounceMillis = 0) { report() })
}
