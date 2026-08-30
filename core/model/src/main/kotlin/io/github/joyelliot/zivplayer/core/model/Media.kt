// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.model

class MediaSource(
    val locator: String,
    val mimeType: String? = null,
    httpHeaders: Map<String, String> = emptyMap(),
) {
    val httpHeaders: Map<String, String> = buildMap {
        putAll(httpHeaders)
    }

    init {
        require(locator.isNotBlank()) { "Media source locator must not be blank." }
        require(mimeType == null || mimeType.isNotBlank()) { "MIME type must not be blank." }
        require(httpHeaders.keys.none(String::isBlank)) { "HTTP header names must not be blank." }
    }

    override fun equals(other: Any?): Boolean =
        other is MediaSource &&
            locator == other.locator &&
            mimeType == other.mimeType &&
            httpHeaders == other.httpHeaders

    override fun hashCode(): Int {
        var result = locator.hashCode()
        result = 31 * result + (mimeType?.hashCode() ?: 0)
        result = 31 * result + httpHeaders.hashCode()
        return result
    }

    override fun toString(): String =
        "MediaSource(locator=$locator, mimeType=$mimeType, httpHeaderNames=${httpHeaders.keys})"
}

data class MediaMetadata(
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    val artworkLocator: String? = null,
)

data class MediaItem(
    val id: MediaId,
    val source: MediaSource,
    val metadata: MediaMetadata = MediaMetadata(),
)

data class QueueItem(
    val id: QueueItemId,
    val media: MediaItem,
)
