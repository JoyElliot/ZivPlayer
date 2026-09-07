// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

/** A controller-owned gesture override. Omitting RATE ends the matching override. */
object TemporarySpeedContract {
    const val ACTION = "io.github.joyelliot.zivplayer.TEMPORARY_SPEED"
    const val TOKEN = "token"
    const val RATE = "rate"
    const val MEDIA_ID = "mediaId"
    const val SEQUENCE = "sequence"
    const val INDEX = "index"
}
