// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.onGloballyPositioned
import kotlin.math.abs
import kotlin.math.sign

/** Track the finger in window space even while the target translates, scales or rotates. */
internal fun Modifier.playerExpansionDrag(state: PlayerPresentationState, travel: Float,
    onSettled: (Boolean) -> Unit, source: String = "mini"): Modifier = composed {
    val settled by rememberUpdatedState(onSettled)
    val distance by rememberUpdatedState(travel)
    var coordinates by remember { mutableStateOf<LayoutCoordinates?>(null) }
    onGloballyPositioned { coordinates = it }.pointerInput(state) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            val start = coordinates?.takeIf { it.isAttached }?.localToWindow(down.position)
                ?: return@awaitEachGesture
            var previous = start
            var started = false
            val velocity = VelocityTracker().apply { addPosition(down.uptimeMillis, start) }
            try {
                while (true) {
                    val event = awaitPointerEvent()
                    val pointer = event.changes.firstOrNull { it.id == down.id } ?: break
                    if (started && !state.dragging) break
                    if (pointer.isConsumed || event.changes.count { it.pressed } > 1) break
                    val position = coordinates?.takeIf { it.isAttached }?.localToWindow(pointer.position) ?: break
                    if (!pointer.pressed) {
                        if (started) {
                            velocity.addPosition(pointer.uptimeMillis, position)
                            pointer.consume()
                            settled(state.settle(-velocity.calculateVelocity().y, distance))
                        }
                        break
                    }
                    var dy = position.y - previous.y
                    if (!started) {
                        val delta = position - start
                        val slop = viewConfiguration.touchSlop
                        if (abs(delta.x) > slop && abs(delta.x) > abs(delta.y)) break
                        if (abs(delta.y) <= slop) {
                            if (awaitPointerEvent(PointerEventPass.Final).changes.any { it.id == down.id && it.isConsumed }) break
                            continue
                        }
                        state.beginDrag(source)
                        started = true
                        dy = delta.y - sign(delta.y) * slop
                    }
                    pointer.consume()
                    velocity.addPosition(pointer.uptimeMillis, position)
                    state.dragBy(-dy, distance)
                    previous = position
                }
            } finally {
                // Disposal or another recognizer may cancel a drag. Do not override a newer
                // button/PiP animation that has already ended this gesture's ownership.
                if (started && state.dragging && state.dragSource == source) settled(state.settle(0f, distance))
            }
        }
    }
}
