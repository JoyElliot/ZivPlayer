// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

internal data class VideoFrame(val centerX: Float, val centerY: Float, val width: Float, val height: Float,
    val rotation: Float = 0f) {
    val boundingWidth: Float get() = abs(width * cos(radians)) + abs(height * sin(radians))
    val boundingHeight: Float get() = abs(width * sin(radians)) + abs(height * cos(radians))
    private val radians get() = Math.toRadians(rotation.toDouble()).toFloat()
}

/** Geometry is independent of playback resources: animation never requests a new Surface. */
internal fun presentationFrame(windowWidth: Float, windowHeight: Float, aspect: Float, mini: VideoFrame,
    expansion: Float, rotation: Float, fill: Boolean = false, stretch: Boolean = false): VideoFrame {
    val ratio = aspect.takeIf { it.isFinite() && it > 0f } ?: (16f / 9f)
    val progress = expansion.coerceIn(0f, 1f)
    val turn = (abs(rotation) / 90f).coerceIn(0f, 1f)
    fun lerp(a: Float, b: Float, t: Float) = a + (b - a) * t
    val width = lerp(windowWidth, windowHeight, turn).coerceAtLeast(1f)
    val height = lerp(windowHeight, windowWidth, turn).coerceAtLeast(1f)
    val scale = if (fill) max(width / ratio, height) else min(width / ratio, height)
    val miniHeight = min(mini.width / ratio, mini.height)
    return VideoFrame(lerp(mini.centerX, windowWidth / 2f, progress),
        lerp(mini.centerY, windowHeight / 2f, progress),
        lerp(miniHeight * ratio, if (stretch) width else scale * ratio, progress),
        lerp(miniHeight, if (stretch) height else scale, progress), rotation * progress)
}

internal fun settleExpanded(progress: Float, upwardVelocity: Float): Boolean = when {
    upwardVelocity > 700f -> true
    upwardVelocity < -700f -> false
    else -> progress >= 0.4f
}
