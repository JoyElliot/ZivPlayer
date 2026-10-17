// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import kotlin.math.ceil
import kotlin.math.pow

internal data class MediaVolumeStep(val index: Int, val gain: Float)

/** Interpolate below the next system step; never amplify past the system's selected level. */
internal fun mediaVolumeStep(level: Float, minimum: Int, maximum: Int,
    decibels: ((Int) -> Float?)? = null): MediaVolumeStep {
    val normalized = if (level.isFinite()) level.coerceIn(0f, 1f) else 0f
    if (maximum <= minimum) return MediaVolumeStep(minimum, 1f)
    if (normalized == 0f) return MediaVolumeStep(minimum, 0f)
    val position = minimum + normalized * (maximum - minimum)
    val upper = ceil(position.toDouble() - 0.00001).toInt().coerceIn(minimum, maximum)
    if (upper == minimum || maximum - minimum >= 50) return MediaVolumeStep(upper, 1f)
    val lower = upper - 1
    val fraction = (position - lower).coerceIn(0f, 1f)
    val highDb = decibels?.invoke(upper)
    val lowDb = decibels?.invoke(lower)
    val gain = when {
        highDb?.isFinite() == true && lowDb?.isFinite() == true && lowDb <= highDb ->
            10.0.pow(((1 - fraction) * (lowDb - highDb) / 20.0)).toFloat()
        lower == minimum -> fraction
        // Without a route curve, assuming linear system gain can make an upward step quieter.
        // Keep the system's monotonic steps instead of inventing a device attenuation curve.
        else -> 1f
    }
    return MediaVolumeStep(upper, gain.coerceIn(0f, 1f))
}
