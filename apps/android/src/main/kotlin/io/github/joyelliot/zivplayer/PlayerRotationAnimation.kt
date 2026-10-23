// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.runtime.*

/** A cancelled animation cannot dispatch its old system orientation request. */
@Composable
internal fun rememberPlayerRotation(
    turned: Boolean,
    basis: PlayerRotationBasis?,
    canvas: PlayerCanvasGeometry,
    pictureInPicture: Boolean,
    pipTransition: Boolean,
    active: Boolean = true,
    immediatePip: Boolean = false,
    collapsed: Boolean = false,
    collapsing: Boolean = false,
    allowWindowRequest: Boolean = true,
    prepareOpening: Boolean = false,
    onOpeningReady: () -> Unit = {},
    onFinished: (Boolean, Boolean) -> Unit,
): State<Float> {
    val animation = remember { Animatable(if (turned) 90f else 0f) }
    val finished by rememberUpdatedState(onFinished)
    val openingReady by rememberUpdatedState(onOpeningReady)
    val target = if (pipTransition) -canvas.compensation else if (turned) 90f else 0f
    LaunchedEffect(turned, basis, pictureInPicture, pipTransition, target, active, immediatePip,
        collapsed, collapsing, allowWindowRequest, prepareOpening) {
        if (pictureInPicture) {
            // PiP draws at zero in its own window. Retain the requested full-player endpoint
            // underneath so exiting PiP cannot briefly revive a half-finished old rotation.
            animation.snapTo(if (turned) 90f else 0f)
            return@LaunchedEffect
        }
        if (pipTransition && immediatePip) { animation.snapTo(target); return@LaunchedEffect }
        if (!active) return@LaunchedEffect
        if (prepareOpening && !pipTransition) {
            // Seed while the video is still at its mini endpoint. Expansion then interpolates
            // size and angle together, instead of finishing a portrait opening before turning.
            animation.snapTo(target)
            openingReady()
            return@LaunchedEffect
        }
        // Collapse already rotated the video with its presentation progress. At the mini endpoint,
        // reset the hidden expanded angle without adding a second animation before WM takes over.
        if (collapsed && !pipTransition) {
            if (turned) return@LaunchedEffect
            animation.snapTo(0f)
        } else if (collapsing && !pipTransition) return@LaunchedEffect
        else animation.animateTo(target, spring(dampingRatio = 1f, stiffness = 360f))
        if (!allowWindowRequest && !pipTransition) return@LaunchedEffect
        // Let the final custom-animation frame reach a drawing opportunity before requesting WM.
        // This is not a promise that SurfaceFlinger has presented that frame.
        withFrameNanos { }
        finished(turned, pipTransition)
    }
    return remember(pictureInPicture, pipTransition, immediatePip, target) {
        derivedStateOf { if (pictureInPicture) 0f else if (pipTransition && immediatePip) target else animation.value }
    }
}
