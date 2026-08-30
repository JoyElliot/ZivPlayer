// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.model

@JvmInline
value class MediaId(val value: String) {
    init {
        require(value.isNotBlank()) { "MediaId must not be blank." }
    }
}

@JvmInline
value class QueueItemId(val value: String) {
    init {
        require(value.isNotBlank()) { "QueueItemId must not be blank." }
    }
}

@JvmInline
value class TrackId(val value: String) {
    init {
        require(value.isNotBlank()) { "TrackId must not be blank." }
    }
}
