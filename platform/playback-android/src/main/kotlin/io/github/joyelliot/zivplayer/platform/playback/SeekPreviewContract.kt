// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.playback

/** Same-app, controller-owned preview. Tokens cannot be reopened after commit/cancel. */
object SeekPreviewContract {
    const val ACTION = "io.github.joyelliot.zivplayer.SEEK_PREVIEW"
    const val TOKEN = "token"
    const val PHASE = "phase"
    const val POSITION = "positionMs"
    const val MEDIA_ID = "mediaId"
    const val SEQUENCE = "sequence"
    const val INDEX = "index"
    const val PREVIEW = 0
    const val COMMIT = 1
    const val CANCEL = 2
    /** Pause presentation while an independent decoder supplies preview frames. */
    const val BEGIN = 3
    /** Lifecycle cancellation restores the position but does not automatically resume playback. */
    const val ABORT = 4
}
