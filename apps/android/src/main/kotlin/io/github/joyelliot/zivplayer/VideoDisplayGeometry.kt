// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.Tracks

internal fun Tracks.videoDisplayAspectRatio(): Float {
    return knownVideoDisplayAspectRatio() ?: (16f / 9f)
}

internal fun Tracks.knownVideoDisplayAspectRatio(): Float? {
    val videoGroups = groups.filter { it.type == C.TRACK_TYPE_VIDEO && it.length > 0 }
    val group = videoGroups.firstOrNull { it.isSelected } ?: videoGroups.firstOrNull() ?: return null
    val index = (0 until group.length).firstOrNull(group::isTrackSelected) ?: 0
    return group.getTrackFormat(index).knownDisplayAspectRatio()
}

internal fun Format.displayAspectRatio(): Float = knownDisplayAspectRatio() ?: (16f / 9f)

internal fun Format.knownDisplayAspectRatio(): Float? {
    if (width <= 0 || height <= 0) return null
    val encodedRatio = width.toFloat() / height * pixelWidthHeightRatio
    val displayRatio = if (rotationDegrees % 180 == 90) 1f / encodedRatio else encodedRatio
    return displayRatio.takeIf { it.isFinite() && it > 0f }
}
