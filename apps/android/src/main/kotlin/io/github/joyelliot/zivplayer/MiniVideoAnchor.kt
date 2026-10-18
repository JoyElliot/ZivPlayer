// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onLayoutRectChanged
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.ZivNavigationStyle

/** A layout slot only: both docks hand their transformed bounds to the one persistent video view. */
@Composable
internal fun MiniVideoAnchor(style: ZivNavigationStyle, onFrame: (VideoFrame) -> Unit) {
    Box(Modifier.size(56.dp, if (style == ZivNavigationStyle.LIQUID_GLASS) 32.dp else 36.dp)
        .testTag("mini_video_anchor")
        // Include ancestor graphics-layer translation/scale, not just measured layout changes.
        // No debounce: the video must follow the glass spring while the navigation is held.
        .onLayoutRectChanged(throttleMillis = 0, debounceMillis = 0) { coordinates ->
            val bounds = coordinates.boundsInRoot
            if (bounds.width > 0 && bounds.height > 0) {
                onFrame(VideoFrame((bounds.left + bounds.right) / 2f, (bounds.top + bounds.bottom) / 2f,
                    bounds.width.toFloat(), bounds.height.toFloat()))
            }
        })
}
