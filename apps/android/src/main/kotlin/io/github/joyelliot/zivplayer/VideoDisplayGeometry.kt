// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.Tracks
import kotlin.math.max
import kotlin.math.roundToInt

internal fun Tracks.videoDisplayAspectRatio(): Float {
    val videoGroups = groups.filter { it.type == C.TRACK_TYPE_VIDEO && it.length > 0 }
    val group = videoGroups.firstOrNull { it.isSelected } ?: videoGroups.firstOrNull() ?: return 16f / 9f
    val index = (0 until group.length).firstOrNull(group::isTrackSelected) ?: 0
    return group.getTrackFormat(index).displayAspectRatio()
}

internal fun Format.displayAspectRatio(): Float {
    if (width <= 0 || height <= 0) return 16f / 9f
    val encodedRatio = width.toFloat() / height * pixelWidthHeightRatio
    val displayRatio = if (kotlin.math.abs(rotationDegrees % 180) == 90) 1f / encodedRatio else encodedRatio
    return displayRatio.takeIf { it.isFinite() && it > 0f } ?: (16f / 9f)
}

/** A stable square-pixel GPU target; window rotation must not enlarge the video. */
internal fun Format.nativeRenderSize(): Pair<Int, Int>? {
    if (width !in 1..16384 || height !in 1..16384) return null
    val pixelRatio = pixelWidthHeightRatio.takeIf { it.isFinite() && it > 0f } ?: 1f
    // Expand the anamorphic axis rather than discarding samples on the other axis.
    val squareWidth = width.toDouble() * max(1.0, pixelRatio.toDouble())
    val squareHeight = height.toDouble() * max(1.0, 1.0 / pixelRatio)
    val scale = (4096.0 / max(squareWidth, squareHeight)).coerceAtMost(1.0)
    val outputWidth = (squareWidth * scale).roundToInt().coerceAtLeast(1)
    val outputHeight = (squareHeight * scale).roundToInt().coerceAtLeast(1)
    return if (kotlin.math.abs(rotationDegrees % 180) == 90) outputHeight to outputWidth else outputWidth to outputHeight
}
