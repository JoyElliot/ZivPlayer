// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

/** The window coordinate system in which a player presentation started. */
data class PlayerRotationBasis(val rotation: Int, val landscape: Boolean) {
    fun targetRotation(turned: Boolean) = (rotation + if (turned) 1 else 0) % 4
    fun isLandscapeAt(value: Int) = landscape != (quarterTurns(rotation, value) % 2 != 0)
}

internal fun quarterTurns(from: Int, to: Int): Int = (to - from + 4) % 4

internal fun isVideoAlignedWithWindow(angle: Float, compensation: Float): Boolean {
    val relative = ((angle + compensation) % 360f + 360f) % 360f
    return relative < 0.01f || relative > 359.99f
}

/** Clockwise display rotation is cancelled at the parent, never animated a second time. */
internal data class PlayerCanvasGeometry(
    val windowWidth: Int,
    val windowHeight: Int,
    val turns: Int,
    val width: Int = if (turns % 2 != 0) windowHeight else windowWidth,
    val height: Int = if (turns % 2 != 0) windowWidth else windowHeight,
) {
    val swapsAxes = turns % 2 != 0
    val compensation = when (turns) { 0 -> 0f; 3 -> 90f; else -> -90f * turns }

    fun windowToCanvas(frame: VideoFrame): VideoFrame {
        val x = frame.centerX - windowWidth / 2f
        val y = frame.centerY - windowHeight / 2f
        val (cx, cy) = when (turns) {
            1 -> -y to x
            2 -> -x to -y
            3 -> y to -x
            else -> x to y
        }
        return VideoFrame(cx + width / 2f, cy + height / 2f,
            if (swapsAxes) frame.height else frame.width,
            if (swapsAxes) frame.width else frame.height)
    }
}

/** Display/configuration callbacks can arrive before the corresponding window measurement. */
internal class PlayerWindowRotationLatch(initialRotation: Int) {
    var rotation = initialRotation
        private set
    private var lastGeometry: PlayerCanvasGeometry? = null

    fun measure(basis: PlayerRotationBasis, observedRotation: Int, width: Int, height: Int,
        multiWindow: Boolean = false): PlayerCanvasGeometry {
        // A 90-degree change must be accompanied by matching window axes. In letterboxed or
        // multi-window modes Android can decline orientation; retain the existing coordinate basis.
        val coherent = multiWindow || width == height || basis.isLandscapeAt(observedRotation) == (width > height)
        if (coherent) {
            rotation = observedRotation
        }
        val geometry = PlayerCanvasGeometry(width, height, quarterTurns(basis.rotation, rotation))
        if (coherent) {
            lastGeometry = geometry
            return geometry
        }
        // The opposite delivery order (new constraints before new rotation) must not reflow the
        // video/controls into the other orientation for one traversal either.
        return geometry.copy(
            width = lastGeometry?.width ?: if (basis.landscape) maxOf(width, height) else minOf(width, height),
            height = lastGeometry?.height ?: if (basis.landscape) minOf(width, height) else maxOf(width, height),
        )
    }
}

/** Pure mapping, using the platform's reverse-default-rotation policy when available. */
internal fun fixedOrientationForRotation(rotation: Int, naturalLandscape: Boolean, reverseDefault: Boolean): Int {
    // ActivityInfo constants: LANDSCAPE=0, PORTRAIT=1, REVERSE_LANDSCAPE=8, REVERSE_PORTRAIT=9.
    val quarter = if (reverseDefault) 3 else 1
    return if (naturalLandscape) when (rotation) {
        0 -> 0
        2 -> 8
        quarter -> 9
        else -> 1
    } else when (rotation) {
        0 -> 1
        2 -> 9
        quarter -> 0
        else -> 8
    }
}
