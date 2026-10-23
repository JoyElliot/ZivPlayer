// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.graphics.Rect
import android.os.Build
import android.view.WindowInsets.Type
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.onLayoutRectChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.media3.session.MediaController
import kotlin.math.max
import kotlin.math.roundToInt

/** One AndroidView call site for mini, expanded, rotation and the system PiP window. */
@Composable
internal fun PersistentVideoStage(controller: MediaController?, canRender: Boolean, aspect: Float,
    window: IntSize, frame: () -> VideoFrame, progress: () -> Float, visible: Boolean = true,
    miniCornerRadius: Dp = 8.dp, onBounds: (Rect) -> Unit) {
    val density = LocalDensity.current
    val edge = remember { max(window.width, window.height).coerceIn(256, 4096) }
    val ratio = aspect.takeIf { it.isFinite() && it > 0 } ?: (16f / 9)
    val width = (if (ratio >= 1) edge.toFloat() else edge * ratio).roundToInt().coerceAtLeast(2)
    val height = (if (ratio >= 1) edge / ratio else edge.toFloat()).roundToInt().coerceAtLeast(2)
    val currentBounds by rememberUpdatedState(onBounds)
    Layout(modifier = Modifier.fillMaxSize().graphicsLayer { clip = true; alpha = if (visible) 1f else 0f }, content = {
        PlayerVideoSurface(controller, canRender, Modifier.onLayoutRectChanged(0, 0) { coordinates ->
            // Includes both the animated video layer and the system-direction compensation.
            val bounds = coordinates.boundsInWindow
            if (bounds.width > 0 && bounds.height > 0) currentBounds(Rect(bounds.left, bounds.top, bounds.right, bounds.bottom))
        }, ratio)
    }) { measurables, constraints ->
        val video = measurables.single().measure(Constraints.fixed(width, height))
        layout(constraints.maxWidth, constraints.maxHeight) {
            video.placeWithLayer((constraints.maxWidth - width) / 2, (constraints.maxHeight - height) / 2) {
                val current = frame()
                transformOrigin = TransformOrigin.Center
                scaleX = current.width / width
                scaleY = current.height / height
                rotationZ = current.rotation
                translationX = current.centerX - constraints.maxWidth / 2f
                translationY = current.centerY - constraints.maxHeight / 2f
                val radius = with(density) { (miniCornerRadius.toPx() * (1 - progress()) / scaleX.coerceAtLeast(0.001f)).toDp() }
                shape = RoundedCornerShape(radius)
                clip = true
            }
        }
    }
}

/** Draw and hit-test controls in the same rotating coordinate space. */
@Composable
internal fun RotatingPlayerViewport(rotation: Float, modifier: Modifier = Modifier, visible: Boolean = true,
    content: @Composable () -> Unit) {
    RotatingPlayerViewport({ rotation }, rotation, modifier, visible, content)
}

/** Measure the destination once; intermediate frames transform that fixed control layout. */
@Composable
internal fun RotatingPlayerViewport(rotation: () -> Float, layoutRotation: Float,
    modifier: Modifier = Modifier, visible: Boolean = true, content: @Composable () -> Unit) {
    Layout(modifier = modifier.fillMaxSize(), content = content) { measurables, constraints ->
        // Gate this measure policy itself: a child policy can be invalidated independently
        // even when an outer layout modifier no longer measures it.
        if (!visible) return@Layout layout(constraints.maxWidth, constraints.maxHeight) { }
        val turn = rotationAxisSwap(layoutRotation)
        val width = (constraints.maxWidth + (constraints.maxHeight - constraints.maxWidth) * turn).roundToInt()
        val height = (constraints.maxHeight + (constraints.maxWidth - constraints.maxHeight) * turn).roundToInt()
        val children = measurables.map { it.measure(Constraints.fixed(width, height)) }
        layout(constraints.maxWidth, constraints.maxHeight) {
            children.forEach { child -> child.placeWithLayer((constraints.maxWidth - width) / 2,
                (constraints.maxHeight - height) / 2) {
                val angle = rotation()
                val currentTurn = rotationAxisSwap(angle)
                rotationZ = angle
                scaleX = (constraints.maxWidth + (constraints.maxHeight - constraints.maxWidth) * currentTurn) / width
                scaleY = (constraints.maxHeight + (constraints.maxWidth - constraints.maxHeight) * currentTurn) / height
            } }
        }
    }
}

/** Only the current video rectangle takes input; the rest of the home page stays interactive. */
@Composable
internal fun VideoExpansionTouchTarget(frame: () -> VideoFrame, state: PlayerPresentationState, travel: Float,
    onOpen: () -> Unit, onSettled: (Boolean) -> Unit) {
    // A stable-size node with the same layer transform as the video keeps hit testing aligned
    // without remeasuring the screen on every animation frame.
    Layout(content = {
        Box(Modifier.fillMaxSize().playerExpansionDrag(state, travel, onSettled, source = "video")
            .clickable(onClick = onOpen))
    }) { measurables, constraints ->
        val target = measurables.single().measure(constraints)
        layout(constraints.maxWidth, constraints.maxHeight) {
            target.placeWithLayer(0, 0) {
                val current = frame()
                transformOrigin = TransformOrigin.Center
                scaleX = current.width / target.width.coerceAtLeast(1)
                scaleY = current.height / target.height.coerceAtLeast(1)
                rotationZ = current.rotation
                translationX = current.centerX - target.width / 2f
                translationY = current.centerY - target.height / 2f
            }
        }
    }
}

@Composable
internal fun playerControlInsets(rotation: Float, basis: PlayerRotationBasis?, canvas: PlayerCanvasGeometry): WindowInsets {
    val source = WindowInsets.safeDrawing
    val density = LocalDensity.current
    val view = LocalView.current
    val latch = remember(basis, canvas.width, canvas.height, density) { PlayerControlInsetsLatch() }
    val currentCanvas = rememberUpdatedState(canvas)
    val currentRotation = rememberUpdatedState(rotation)
    return remember(source, latch, view) {
        object : WindowInsets {
            private fun area(density: Density): PlayerSafeArea {
                val canvas = currentCanvas.value
                // Observe Compose insets so a first dispatch schedules another measurement.
                val observed = PlayerSafeArea(source.getLeft(density, LayoutDirection.Ltr), source.getTop(density),
                    source.getRight(density, LayoutDirection.Ltr), source.getBottom(density))
                // Compose starts at zero; seed from the window's dispatched snapshot instead.
                val snapshot = view.rootWindowInsets
                val frame = if (Build.VERSION.SDK_INT >= 35) snapshot?.frame else null
                val ready = snapshot != null && (frame == null ||
                    (frame.width == canvas.windowWidth && frame.height == canvas.windowHeight))
                val native = snapshot?.getInsets(Type.systemBars() or Type.displayCutout() or Type.ime())
                return latch.resolve({ native?.let { PlayerSafeArea(it.left, it.top, it.right, it.bottom) } ?: observed },
                    canvas.turns, currentRotation.value, sourceReady = ready)
            }
            override fun getLeft(density: Density, layoutDirection: LayoutDirection) =
                area(density).left
            override fun getTop(density: Density) = area(density).top
            override fun getRight(density: Density, layoutDirection: LayoutDirection) =
                area(density).right
            override fun getBottom(density: Density) = area(density).bottom
        }
    }
}
