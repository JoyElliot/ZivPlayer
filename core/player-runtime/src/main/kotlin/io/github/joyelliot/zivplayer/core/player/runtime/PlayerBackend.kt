// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player.runtime

import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.SubtitleSource
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.VolumePercent
import io.github.joyelliot.zivplayer.core.player.PlayerCapabilities
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot
import kotlinx.coroutines.flow.Flow

@JvmInline
value class LoadGeneration(val value: Long) {
    init {
        require(value > 0L) { "Load generation must be positive." }
    }
}

@JvmInline
value class SeekGeneration(val value: Long) {
    init {
        require(value > 0L) { "Seek generation must be positive." }
    }
}

data class BackendLoadRequest(
    val generation: LoadGeneration,
    val item: QueueItem,
    val startPosition: Milliseconds,
)

data class BackendSeekRequest(
    val generation: LoadGeneration,
    val seekGeneration: SeekGeneration,
    val position: Milliseconds,
)

sealed interface BackendEvent {
    val generation: LoadGeneration

    data class Prepared(
        override val generation: LoadGeneration,
        val duration: Milliseconds?,
        val tracks: TrackSnapshot = TrackSnapshot.EMPTY,
        val capabilities: PlayerCapabilities = PlayerCapabilities.NONE,
    ) : BackendEvent

    data class PositionChanged(
        override val generation: LoadGeneration,
        val position: Milliseconds,
        val duration: Milliseconds?,
        val bufferedPosition: Milliseconds?,
        /** Latest completed or in-flight seek epoch that affected this sample. */
        val seekGeneration: SeekGeneration? = null,
    ) : BackendEvent

    data class TracksChanged(
        override val generation: LoadGeneration,
        val tracks: TrackSnapshot,
    ) : BackendEvent

    data class PlaybackStarted(
        override val generation: LoadGeneration,
    ) : BackendEvent

    data class PlaybackPaused(
        override val generation: LoadGeneration,
    ) : BackendEvent

    data class SeekCompleted(
        override val generation: LoadGeneration,
        val seekGeneration: SeekGeneration,
        val position: Milliseconds,
    ) : BackendEvent

    data class BufferingChanged(
        override val generation: LoadGeneration,
        val buffering: Boolean,
    ) : BackendEvent

    data class Ended(
        override val generation: LoadGeneration,
    ) : BackendEvent

    data class Failure(
        override val generation: LoadGeneration,
        val error: PlayerError,
    ) : BackendEvent
}

interface PlayerBackend {
    /** Must remain active until [close]; early completion is a terminal failure. */
    val events: Flow<BackendEvent>

    suspend fun load(request: BackendLoadRequest)

    suspend fun play()

    suspend fun pause()

    suspend fun seekTo(request: BackendSeekRequest)

    suspend fun stop()

    suspend fun setVolume(volume: VolumePercent)

    suspend fun setPlaybackRate(playbackRate: PlaybackRatePermille)

    suspend fun setMuted(muted: Boolean)

    suspend fun selectTrack(kind: TrackKind, trackId: TrackId?)

    suspend fun addSubtitle(source: SubtitleSource)

    suspend fun close()
}
