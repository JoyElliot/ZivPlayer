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

    /** Changes surrounding items while retaining the exact current item and playback resources. */
    class EditQueue(items: List<QueueItem>) : PlayerCommand {
        val items: List<QueueItem> = items.toList()
    }

    data object Play : PlayerCommand

    data object Pause : PlayerCommand

    /** Temporarily stops presentation without replacing the user's play/pause intent. */
    data class SetPlaybackSuppressed(val suppressed: Boolean) : PlayerCommand

    data class SeekTo(val position: Milliseconds, val precision: SeekPrecision = SeekPrecision.EXACT,
                      val completionId: Long? = null) : PlayerCommand {
        init { require(completionId == null || completionId > 0) }
    }

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

/** Interactive previews may show a nearby keyframe; committed seeks retain exact positioning. */
enum class SeekPrecision { KEYFRAME, EXACT }

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
