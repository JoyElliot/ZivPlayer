// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import kotlin.math.roundToInt
import kotlin.math.roundToLong

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
