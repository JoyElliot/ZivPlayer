// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.Constraints

/** Keep one content subtree and one video view while the system changes the window's axes. */
@Composable
internal fun PlayerWindowCanvas(
    basis: PlayerRotationBasis?,
    pictureInPicture: Boolean,
    onWindowRotation: (Int) -> Unit,
    content: @Composable (PlayerCanvasGeometry) -> Unit,
) {
    val view = LocalView.current
    val activity = remember(view) { view.context.findActivity() }
    // Subscribe to configuration changes; Display.getRotation alone is not Compose state.
    val configuration = LocalConfiguration.current
    val latch = remember(basis) { PlayerWindowRotationLatch(basis?.rotation ?: 0) }
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val observed = view.display?.rotation ?: basis?.rotation ?: 0
        val geometry = if (basis != null && !pictureInPicture && configuration.orientation != 0) {
            latch.measure(basis, observed, constraints.maxWidth, constraints.maxHeight, activity?.isInMultiWindowMode == true)
        } else PlayerCanvasGeometry(constraints.maxWidth, constraints.maxHeight, 0)
        SideEffect { if (basis != null && !pictureInPicture) onWindowRotation(latch.rotation) }
        Layout(content = { content(geometry) }) { measurables, window ->
            val children = measurables.map { it.measure(Constraints.fixed(geometry.width, geometry.height)) }
            layout(window.maxWidth, window.maxHeight) {
                children.forEach { child ->
                    child.placeWithLayer((window.maxWidth - geometry.width) / 2,
                        (window.maxHeight - geometry.height) / 2) {
                        rotationZ = geometry.compensation
                    }
                }
            }
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> if (baseContext !== this) baseContext.findActivity() else null
    else -> null
}
