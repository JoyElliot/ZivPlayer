// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.model

@JvmInline
value class Milliseconds(val value: Long) : Comparable<Milliseconds> {
    init {
        require(value >= 0L) { "Milliseconds must not be negative." }
    }

    override fun compareTo(other: Milliseconds): Int = value.compareTo(other.value)

    companion object {
        val ZERO = Milliseconds(0L)
    }
}

@JvmInline
value class SignedMilliseconds(val value: Long)

@JvmInline
value class VolumePercent(val value: Int) {
    init {
        require(value in 0..100) { "Volume must be between 0 and 100 percent." }
    }

    companion object {
        val MUTED = VolumePercent(0)
        val FULL = VolumePercent(100)
    }
}

@JvmInline
value class PlaybackRatePermille(val value: Int) {
    init {
        require(value in 250..4_000) { "Playback rate must be between 0.25x and 4.0x." }
    }

    companion object {
        val NORMAL = PlaybackRatePermille(1_000)
    }
}
