// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import java.util.concurrent.atomic.AtomicLong

object PlaybackRequestMetadata {
    const val TOKEN_EXTRA = "io.github.joyelliot.zivplayer.extra.PLAYBACK_REQUEST_TOKEN"
    const val SEQUENCE_EXTRA = "io.github.joyelliot.zivplayer.extra.PLAYBACK_REQUEST_SEQUENCE"
}

/** Process-scoped ordering shared by the app UI and its in-process playback service. */
object PlaybackRequestSequencer {
    private val latest = AtomicLong(0L)

    fun next(): Long = latest.incrementAndGet()

    fun current(): Long = latest.get()

    fun invalidate(): Long = next()
}
