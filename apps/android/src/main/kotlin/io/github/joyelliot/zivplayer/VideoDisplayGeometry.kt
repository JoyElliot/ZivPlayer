// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.Tracks

internal fun Tracks.videoDisplayAspectRatio(): Float {
    val videoGroups = groups.filter { it.type == C.TRACK_TYPE_VIDEO && it.length > 0 }
    val group = videoGroups.firstOrNull { it.isSelected } ?: videoGroups.firstOrNull() ?: return 16f / 9f
    val index = (0 until group.length).firstOrNull(group::isTrackSelected) ?: 0
    return group.getTrackFormat(index).displayAspectRatio()
}

internal fun Format.displayAspectRatio(): Float {
    if (width <= 0 || height <= 0) return 16f / 9f
    val encodedRatio = width.toFloat() / height * pixelWidthHeightRatio
    val displayRatio = if (rotationDegrees % 180 == 90) 1f / encodedRatio else encodedRatio
    return displayRatio.takeIf { it.isFinite() && it > 0f } ?: (16f / 9f)
}
