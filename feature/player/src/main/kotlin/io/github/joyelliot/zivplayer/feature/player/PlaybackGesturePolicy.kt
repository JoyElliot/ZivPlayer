// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import kotlin.math.roundToInt
import kotlin.math.roundToLong

/** The outer thirds seek; the middle third retains play/pause. Step is always five seconds. */
internal fun doubleTapSeekDelta(horizontalFraction: Float): Long = when {
    horizontalFraction < 1f / 3f -> -5_000L
    horizontalFraction > 2f / 3f -> 5_000L
    else -> 0L
}

internal fun doubleTapSeekPosition(positionMs: Long, durationMs: Long, deltaMs: Long): Long =
    (positionMs.coerceIn(0L, durationMs.coerceAtLeast(0L)).toDouble() + deltaMs)
        .coerceIn(0.0, durationMs.coerceAtLeast(0L).toDouble()).roundToLong()

/** An upward drag over two thirds of the screen covers the full brightness/volume range. */
internal fun gestureLevel(start: Float, verticalFraction: Float): Float =
    if (!verticalFraction.isFinite()) start.coerceIn(0f, 1f)
    else (start - verticalFraction * 1.5f).coerceIn(0f, 1f)

/** A full-width swipe seeks at most two minutes, with a fixed origin for the whole gesture. */
internal fun gestureSeekPosition(startMs: Long, durationMs: Long, distanceFraction: Float): Long {
    if (durationMs <= 0L || !distanceFraction.isFinite()) return startMs.coerceAtLeast(0L)
    val start = startMs.coerceIn(0L, durationMs)
    val window = minOf(durationMs, 120_000L)
    return (start.toDouble() + distanceFraction.toDouble() * window)
        .coerceIn(0.0, durationMs.toDouble()).roundToLong()
}

/** Long press starts at 2x; horizontal movement selects slow motion through 4x in 0.25x steps. */
internal fun gesturePlaybackSpeed(distanceFraction: Float): Float {
    if (!distanceFraction.isFinite()) return 2f
    val speed = 2f + distanceFraction.coerceIn(-1f, 1f) * 4f
    return (speed * 4f).roundToInt().div(4f).coerceIn(0.25f, 4f)
}
