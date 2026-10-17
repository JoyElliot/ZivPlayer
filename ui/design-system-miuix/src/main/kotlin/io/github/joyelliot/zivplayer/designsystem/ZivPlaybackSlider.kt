// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.*
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import top.yukonga.miuix.kmp.theme.MiuixTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.key.*
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp

/** A thin video timeline with a full 48 dp touch target and accessible adjustment. */
@Composable
fun ZivPlaybackSlider(
    value: Float,
    onValueChange: (Float) -> Unit,
    onValueChangeFinished: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    bufferedValue: Float = value,
    description: String,
    onCancel: () -> Unit = {},
    trackColor: Color = Color.White,
    activeColor: Color = MiuixTheme.colorScheme.primary,
    onDragValueChange: (Float) -> Unit = {},
) {
    val position by rememberUpdatedState(value.coerceIn(0f, 1f))
    val change by rememberUpdatedState(onValueChange)
    val finish by rememberUpdatedState(onValueChangeFinished)
    val cancel by rememberUpdatedState(onCancel)
    val preview by rememberUpdatedState(onDragValueChange)
    var dragging by remember { mutableStateOf(false) }
    val emphasis by animateFloatAsState(if (dragging) 1f else 0f,
        spring(dampingRatio = 1f, stiffness = 600f), label = "timeline emphasis")
    Canvas(modifier.height(48.dp).semantics {
        contentDescription = description
        progressBarRangeInfo = ProgressBarRangeInfo(value.coerceIn(0f, 1f), 0f..1f)
        if (!enabled) disabled() else setProgress { next -> change(next.coerceIn(0f, 1f)); finish(); true }
    }.onKeyEvent { event ->
        if (!enabled || event.type != KeyEventType.KeyDown) false else when (event.key) {
            Key.DirectionLeft, Key.DirectionRight -> {
                change((position + if (event.key == Key.DirectionRight) 0.05f else -0.05f).coerceIn(0f, 1f))
                finish()
                true
            }
            else -> false
        }
    }.focusable(enabled).pointerInput(enabled) {
        if (!enabled) return@pointerInput
        awaitEachGesture {
            var committed = false
            var started = false
            var dragStarted = false
            try {
                val down = awaitFirstDown()
                started = true
                down.consume()
                dragging = true
                val inset = 8.dp.toPx()
                fun fraction(x: Float) = ((x - inset) / (size.width - 2 * inset).coerceAtLeast(1f)).coerceIn(0f, 1f)
                change(fraction(down.position.x))
                while (true) {
                    val event = awaitPointerEvent()
                    val pointer = event.changes.firstOrNull { it.id == down.id } ?: break
                    if (pointer.isConsumed || event.changes.count { it.pressed } > 1) break
                    change(fraction(pointer.position.x))
                    if ((pointer.position - down.position).getDistance() > viewConfiguration.touchSlop) dragStarted = true
                    if (pointer.pressed && dragStarted) preview(fraction(pointer.position.x))
                    pointer.consume()
                    if (!pointer.pressed) { finish(); committed = true; break }
                }
            } finally {
                dragging = false
                if (started && !committed) cancel()
            }
        }
    }) {
        val inset = 8.dp.toPx()
        val start = Offset(inset, size.height / 2)
        val end = Offset((size.width - inset).coerceAtLeast(inset), start.y)
        val width = (end.x - start.x).coerceAtLeast(0f)
        val alpha = if (enabled) 1f else 0.35f
        val thickness = (3 + emphasis * 2).dp.toPx()
        drawLine(trackColor.copy(alpha = 0.18f * alpha), start, end, thickness, StrokeCap.Round)
        drawLine(trackColor.copy(alpha = 0.35f * alpha), start,
            Offset(start.x + bufferedValue.coerceIn(0f, 1f) * width, start.y), thickness, StrokeCap.Round)
        val thumb = Offset(start.x + position * width, start.y)
        drawLine(activeColor.copy(alpha = alpha), start, thumb, thickness, StrokeCap.Round)
        drawCircle(activeColor.copy(alpha = alpha), (5 + emphasis * 2).dp.toPx(), thumb)
    }
}
