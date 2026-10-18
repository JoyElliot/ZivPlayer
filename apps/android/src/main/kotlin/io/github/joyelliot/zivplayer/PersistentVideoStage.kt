// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.graphics.Rect
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInWindow
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.IntOffset
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
    window: IntSize, frame: VideoFrame, progress: Float, visible: Boolean = true,
    miniCornerRadius: Dp = 8.dp, onBounds: (Rect) -> Unit) {
    val density = LocalDensity.current
    val edge = remember { max(window.width, window.height).coerceIn(256, 4096) }
    val ratio = aspect.takeIf { it.isFinite() && it > 0 } ?: (16f / 9)
    val width = (if (ratio >= 1) edge.toFloat() else edge * ratio).roundToInt().coerceAtLeast(2)
    val height = (if (ratio >= 1) edge / ratio else edge.toFloat()).roundToInt().coerceAtLeast(2)
    var windowOrigin by remember { mutableStateOf(Offset.Zero) }
    val currentBounds by rememberUpdatedState(onBounds)
    SideEffect {
        val bounds = Rect((windowOrigin.x + frame.centerX - frame.boundingWidth / 2).roundToInt(),
            (windowOrigin.y + frame.centerY - frame.boundingHeight / 2).roundToInt(),
            (windowOrigin.x + frame.centerX + frame.boundingWidth / 2).roundToInt(),
            (windowOrigin.y + frame.centerY + frame.boundingHeight / 2).roundToInt())
        if (bounds.intersect(windowOrigin.x.roundToInt(), windowOrigin.y.roundToInt(),
                (windowOrigin.x + window.width).roundToInt(), (windowOrigin.y + window.height).roundToInt())) currentBounds(bounds)
    }
    Layout(modifier = Modifier.fillMaxSize().graphicsLayer { clip = true; alpha = if (visible) 1f else 0f }
        .onGloballyPositioned { windowOrigin = it.positionInWindow() }, content = {
        PlayerVideoSurface(controller, canRender, Modifier, ratio)
    }) { measurables, constraints ->
        val video = measurables.single().measure(Constraints.fixed(width, height))
        layout(constraints.maxWidth, constraints.maxHeight) {
            video.placeWithLayer((constraints.maxWidth - width) / 2, (constraints.maxHeight - height) / 2) {
                transformOrigin = TransformOrigin.Center
                scaleX = frame.width / width
                scaleY = frame.height / height
                rotationZ = frame.rotation
                translationX = frame.centerX - constraints.maxWidth / 2f
                translationY = frame.centerY - constraints.maxHeight / 2f
                val radius = with(density) { (miniCornerRadius.toPx() * (1 - progress) / scaleX.coerceAtLeast(0.001f)).toDp() }
                shape = RoundedCornerShape(radius)
                clip = true
            }
        }
    }
}

/** Draw and hit-test controls in the same rotating coordinate space. */
@Composable
internal fun RotatingPlayerViewport(rotation: Float, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Layout(modifier = modifier.fillMaxSize(), content = content) { measurables, constraints ->
        val turn = (kotlin.math.abs(rotation) / 90f).coerceIn(0f, 1f)
        val width = (constraints.maxWidth + (constraints.maxHeight - constraints.maxWidth) * turn).roundToInt()
        val height = (constraints.maxHeight + (constraints.maxWidth - constraints.maxHeight) * turn).roundToInt()
        val children = measurables.map { it.measure(Constraints.fixed(width, height)) }
        layout(constraints.maxWidth, constraints.maxHeight) {
            children.forEach { child -> child.placeWithLayer((constraints.maxWidth - width) / 2,
                (constraints.maxHeight - height) / 2) { rotationZ = rotation } }
        }
    }
}

/** Only the current video rectangle takes input; the rest of the home page stays interactive. */
@Composable
internal fun VideoExpansionTouchTarget(frame: VideoFrame, state: PlayerPresentationState, travel: Float,
    onOpen: () -> Unit, onSettled: (Boolean) -> Unit) {
    val density = LocalDensity.current
    Box(Modifier.offset { IntOffset((frame.centerX - frame.boundingWidth / 2).roundToInt(),
        (frame.centerY - frame.boundingHeight / 2).roundToInt()) }
        .size(with(density) { frame.boundingWidth.toDp() }, with(density) { frame.boundingHeight.toDp() })
        .playerExpansionDrag(state, travel, onSettled, source = "video").clickable(onClick = onOpen))
}

internal fun Modifier.playerExpansionDrag(state: PlayerPresentationState, travel: Float,
    onSettled: (Boolean) -> Unit, source: String = "mini"): Modifier = composed {
    val settled by rememberUpdatedState(onSettled)
    val distance by rememberUpdatedState(travel)
    var origin by remember { mutableStateOf(Offset.Zero) }
    onGloballyPositioned { origin = it.positionInRoot() }.pointerInput(state) {
        val velocity = VelocityTracker()
        detectVerticalDragGestures(
            onDragStart = { velocity.resetTracking(); state.beginDrag(source) },
            onDragEnd = { settled(state.settle(-velocity.calculateVelocity().y, distance)) },
            onDragCancel = { settled(state.settle(0f, distance)) },
            onVerticalDrag = { change, dy ->
                velocity.addPosition(change.uptimeMillis, change.position + origin)
                change.consume()
                state.dragBy(-dy, distance)
            },
        )
    }
}

@Composable
internal fun playerControlInsets(rotation: Float): WindowInsets {
    val source = WindowInsets.safeDrawing
    return remember(source, rotation >= 45f) {
        object : WindowInsets {
            private val rotated = rotation >= 45f
            override fun getLeft(density: Density, layoutDirection: LayoutDirection) =
                if (rotated) source.getTop(density) else source.getLeft(density, layoutDirection)
            override fun getTop(density: Density) =
                if (rotated) source.getRight(density, LayoutDirection.Ltr) else source.getTop(density)
            override fun getRight(density: Density, layoutDirection: LayoutDirection) =
                if (rotated) source.getBottom(density) else source.getRight(density, layoutDirection)
            override fun getBottom(density: Density) =
                if (rotated) source.getLeft(density, LayoutDirection.Ltr) else source.getBottom(density)
        }
    }
}
