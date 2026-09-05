// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player

import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.VolumePercent

enum class PlayerStatus {
    IDLE,
    LOADING,
    READY,
    PLAYING,
    PAUSED,
    BUFFERING,
    ENDED,
    ERROR,
    CLOSED,
}

enum class RepeatMode {
    OFF,
    ONE,
    ALL,
}

data class PlaybackTimeline(
    val position: Milliseconds = Milliseconds.ZERO,
    val duration: Milliseconds? = null,
    val bufferedPosition: Milliseconds? = null,
) {
    init {
        require(duration == null || position <= duration) { "Position must not exceed duration." }
        require(duration == null || bufferedPosition == null || bufferedPosition <= duration) {
            "Buffered position must not exceed duration."
        }
    }

    companion object {
        fun normalized(
            position: Milliseconds,
            duration: Milliseconds?,
            bufferedPosition: Milliseconds?,
        ): PlaybackTimeline {
            val boundedPosition = duration?.let { minOf(position, it) } ?: position
            val boundedBuffered = bufferedPosition?.let { buffered ->
                duration?.let { minOf(buffered, it) } ?: buffered
            }?.let { maxOf(it, boundedPosition) }
            return PlaybackTimeline(boundedPosition, duration, boundedBuffered)
        }
    }
}

@ConsistentCopyVisibility
data class PlaybackQueue private constructor(
    val items: List<QueueItem>,
    val currentIndex: Int?,
) {
    val currentItem: QueueItem?
        get() = currentIndex?.let(items::get)

    init {
        require(items.map(QueueItem::id).distinct().size == items.size) {
            "Queue item IDs must be unique."
        }
        require(
            (items.isEmpty() && currentIndex == null) ||
                (items.isNotEmpty() && currentIndex != null && currentIndex in items.indices),
        ) { "Current queue index must identify an existing item." }
    }

    fun withCurrentIndex(index: Int): PlaybackQueue = of(items, index)

    companion object {
        val EMPTY = PlaybackQueue(emptyList(), null)

        fun of(items: List<QueueItem>, currentIndex: Int): PlaybackQueue =
            PlaybackQueue(items.toList(), currentIndex)
    }
}

enum class PlayerCapability {
    PLAY,
    PAUSE,
    STOP,
    SEEK,
    SKIP_NEXT,
    SKIP_PREVIOUS,
    SET_REPEAT,
    SET_VOLUME,
    SET_RATE,
    SET_MUTED,
    SELECT_TRACK,
    ADD_SUBTITLE,
}

@ConsistentCopyVisibility
data class PlayerCapabilities private constructor(
    val supported: Set<PlayerCapability>,
    val backendAvailable: Set<PlayerCapability>,
    val available: Set<PlayerCapability>,
) {
    init {
        require(supported.containsAll(backendAvailable)) {
            "Backend-available capabilities must be supported."
        }
        require(supported.containsAll(available)) { "Available capabilities must be supported." }
        require(backendAvailable.containsAll(available)) {
            "Available capabilities must be available from the backend."
        }
    }

    companion object {
        val NONE = PlayerCapabilities(emptySet(), emptySet(), emptySet())

        fun of(
            supported: Set<PlayerCapability>,
            backendAvailable: Set<PlayerCapability> = supported,
            available: Set<PlayerCapability> = backendAvailable,
        ): PlayerCapabilities = PlayerCapabilities(
            supported = supported.toSet(),
            backendAvailable = backendAvailable.toSet(),
            available = available.toSet(),
        )
    }
}

@ConsistentCopyVisibility
data class TrackSnapshot private constructor(
    val available: List<TrackDescriptor>,
    val selected: Map<TrackKind, TrackId?>,
) {
    init {
        require(available.map(TrackDescriptor::id).distinct().size == available.size) {
            "Track IDs must be unique."
        }
        val tracksById = available.associateBy(TrackDescriptor::id)
        selected.forEach { (kind, id) ->
            require(id == null || tracksById[id]?.kind == kind) {
                "Selected track must exist and have the requested kind."
            }
        }
    }

    companion object {
        val EMPTY = TrackSnapshot(emptyList(), emptyMap())

        fun of(
            available: List<TrackDescriptor>,
            selected: Map<TrackKind, TrackId?>,
        ): TrackSnapshot = TrackSnapshot(available.toList(), selected.toMap())
    }
}

data class PlaybackSnapshot(
    val status: PlayerStatus = PlayerStatus.IDLE,
    val playWhenReady: Boolean = false,
    val queue: PlaybackQueue = PlaybackQueue.EMPTY,
    val timeline: PlaybackTimeline = PlaybackTimeline(),
    val repeatMode: RepeatMode = RepeatMode.OFF,
    val tracks: TrackSnapshot = TrackSnapshot.EMPTY,
    val capabilities: PlayerCapabilities = PlayerCapabilities.NONE,
    val volume: VolumePercent = VolumePercent.FULL,
    val playbackRate: PlaybackRatePermille = PlaybackRatePermille.NORMAL,
    val muted: Boolean = false,
    val error: PlayerError? = null,
    val revision: Long = 0L,
) {
    init {
        require(revision >= 0L) { "State revision must not be negative." }
        require(status != PlayerStatus.PLAYING || playWhenReady) {
            "PLAYING requires playWhenReady."
        }
        require((status == PlayerStatus.ERROR) == (error != null)) {
            "Only ERROR state may contain an active error."
        }
        require(
            status == PlayerStatus.IDLE ||
                status == PlayerStatus.ERROR ||
                status == PlayerStatus.CLOSED ||
                queue.currentItem != null,
        ) {
            "An active playback state requires a current queue item."
        }
    }
}
