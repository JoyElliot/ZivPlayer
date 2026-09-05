// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player

import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.SubtitleSource
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.VolumePercent

sealed interface PlayerCommand {
    class SetQueue(
        items: List<QueueItem>,
        val startIndex: Int = 0,
        val startPosition: Milliseconds = Milliseconds.ZERO,
        val playWhenReady: Boolean = false,
    ) : PlayerCommand {
        val items: List<QueueItem> = items.toList()
    }

    data object Play : PlayerCommand

    data object Pause : PlayerCommand

    data class SeekTo(val position: Milliseconds) : PlayerCommand

    data object Stop : PlayerCommand

    data object SkipNext : PlayerCommand

    data object SkipPrevious : PlayerCommand

    data class SetRepeatMode(val repeatMode: RepeatMode) : PlayerCommand

    data class SetVolume(val volume: VolumePercent) : PlayerCommand

    data class SetPlaybackRate(val playbackRate: PlaybackRatePermille) : PlayerCommand

    data class SetMuted(val muted: Boolean) : PlayerCommand

    data class SelectTrack(
        val kind: TrackKind,
        val trackId: TrackId?,
    ) : PlayerCommand

    data class AddSubtitle(val source: SubtitleSource) : PlayerCommand

    data object ClearQueue : PlayerCommand
}

sealed interface CommandResult {
    val revision: Long

    data class Accepted(
        override val revision: Long,
        val changed: Boolean,
    ) : CommandResult

    data class Rejected(
        override val revision: Long,
        val error: PlayerError,
    ) : CommandResult

    data class Failed(
        override val revision: Long,
        val error: PlayerError,
    ) : CommandResult
}
