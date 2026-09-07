// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import io.github.joyelliot.zivplayer.core.model.Milliseconds

/** A source URI that remains meaningful across process restarts. */
@JvmInline
value class DurableMediaUri(val value: String) {
    init {
        require(value.isNotBlank()) { "Durable media URI must not be blank." }
        require(!value.contains(PROCESS_FILE_DESCRIPTOR_PATH) &&
            !value.startsWith("fd://", ignoreCase = true) &&
            !value.startsWith("fdclose://", ignoreCase = true)) {
            "A process-local file descriptor cannot be a durable media URI."
        }
    }

    private companion object {
        const val PROCESS_FILE_DESCRIPTOR_PATH = "/proc/self/fd/"
    }
}

@JvmInline
value class EpochMilliseconds(val value: Long) : Comparable<EpochMilliseconds> {
    init {
        require(value >= 0L) { "Epoch milliseconds must not be negative." }
    }

    override fun compareTo(other: EpochMilliseconds): Int = value.compareTo(other.value)
}

/** Input recorded when the user explicitly opens a durable media URI. */
data class MediaRegistration(
    val sourceUri: DurableMediaUri,
    val mimeType: String? = null,
    val metadata: MediaMetadata = MediaMetadata(),
    val openedAt: EpochMilliseconds,
    /** Queue preparation allocates stable identity without appearing in recently played media. */
    val visibleInHistory: Boolean = true,
) {
    init {
        require(mimeType == null || mimeType.isNotBlank()) { "MIME type must not be blank." }
    }
}

/** Persistent identity and display data, independent from queue instances and transport locators. */
data class MediaRecord(
    val id: MediaId,
    val sourceUri: DurableMediaUri,
    val mimeType: String? = null,
    val metadata: MediaMetadata = MediaMetadata(),
    val addedAt: EpochMilliseconds,
    val lastOpenedAt: EpochMilliseconds,
) {
    init {
        require(mimeType == null || mimeType.isNotBlank()) { "MIME type must not be blank." }
    }
}

data class PlaybackCheckpoint(
    val mediaId: MediaId,
    val position: Milliseconds,
    val duration: Milliseconds? = null,
    val completed: Boolean = false,
    val updatedAt: EpochMilliseconds,
)

data class RecentMedia(
    val record: MediaRecord,
    val checkpoint: PlaybackCheckpoint? = null,
)
