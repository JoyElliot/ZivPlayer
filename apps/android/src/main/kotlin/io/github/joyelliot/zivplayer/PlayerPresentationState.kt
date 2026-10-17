// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.animation.core.AnimationState
import androidx.compose.animation.core.animateTo
import androidx.compose.animation.core.spring
import androidx.compose.runtime.*
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** A new gesture takes over the current frame, including an unfinished spring. */
@Stable
internal class PlayerPresentationState(private val scope: CoroutineScope, initiallyExpanded: Boolean) {
    var progress by mutableFloatStateOf(if (initiallyExpanded) 1f else 0f)
        private set
    var dragging by mutableStateOf(false)
        private set
    var dragSource by mutableStateOf("")
        private set
    private var animation: Job? = null
    private var velocity = 0f

    fun animateTo(expanded: Boolean, initialVelocity: Float = velocity) {
        animation?.cancel()
        dragging = false
        animation = scope.launch {
            AnimationState(progress, initialVelocity.coerceIn(-4f, 4f)).animateTo(if (expanded) 1f else 0f,
                spring(dampingRatio = 1f, stiffness = 420f, visibilityThreshold = 0.001f)) {
                progress = value.coerceIn(0f, 1f)
                this@PlayerPresentationState.velocity = this.velocity
            }
            velocity = 0f
        }
    }

    fun beginDrag(source: String) { animation?.cancel(); dragSource = source; dragging = true; velocity = 0f }
    fun dragBy(upwardPixels: Float, travel: Float) {
        progress = (progress + upwardPixels / travel.coerceAtLeast(1f)).coerceIn(0f, 1f)
    }
    fun settle(upwardVelocity: Float, travel: Float): Boolean = settleExpanded(progress, upwardVelocity).also {
        animateTo(it, upwardVelocity / travel.coerceAtLeast(1f))
    }
}
