// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import java.util.concurrent.atomic.AtomicLong

object PlaybackRequestMetadata {
    const val TOKEN_EXTRA = "io.github.joyelliot.zivplayer.extra.PLAYBACK_REQUEST_TOKEN"
    const val SEQUENCE_EXTRA = "io.github.joyelliot.zivplayer.extra.PLAYBACK_REQUEST_SEQUENCE"
}

/** Same-app command; document grants remain platform-owned and are never persisted by core. */
object SubtitleRequestContract {
    const val ACTION_ADD = "io.github.joyelliot.zivplayer.ADD_SUBTITLE"
    const val URI = "subtitle_uri"
    const val TITLE = "subtitle_title"
    const val MEDIA_ID = "target_media_id"
    const val REQUEST_SEQUENCE = "target_request_sequence"
    const val ERROR_MESSAGE = "error_message"
}

object VideoSurfaceRequestContract {
    const val ACTION_RESIZE = "io.github.joyelliot.zivplayer.RESIZE_VIDEO_SURFACE"
    const val TOKEN = "surface_token"
    const val WIDTH = "surface_width"
    const val HEIGHT = "surface_height"
}

/** UI and service share one process. Old Surface callbacks cannot resize a newer render target. */
object VideoSurfaceRequests {
    private val sequence = AtomicLong(0L)
    private val active = AtomicLong(0L)

    fun claim(): Long = sequence.incrementAndGet().also(active::set)
    fun current(): Long = active.get()
    fun isCurrent(token: Long): Boolean = token > 0L && token == active.get()
    fun release(token: Long) { active.compareAndSet(token, 0L) }
}

/** Process-scoped ordering shared by the app UI and its in-process playback service. */
object PlaybackRequestSequencer {
    private val latest = AtomicLong(0L)

    fun next(): Long = latest.incrementAndGet()

    fun current(): Long = latest.get()

    fun invalidate(): Long = next()
}
