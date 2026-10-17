// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

/** Same-UID scrubbing command; every target is fenced to a specific queue installation. */
object ScrubSeekContract {
    const val ACTION = "io.github.joyelliot.zivplayer.SCRUB_SEEK"
    const val MEDIA_ID = "mediaId"
    const val SEQUENCE = "sequence"
    const val INDEX = "index"
    const val POSITION = "positionMs"
    const val EXACT = "exact"
}
