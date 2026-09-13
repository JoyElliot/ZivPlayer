// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.exoplayer

/** Android engine optimization used only while the service owns a scrub lease. */
interface DirectScrubPort {
    fun beginScrubbing()
    fun endScrubbing()
}
