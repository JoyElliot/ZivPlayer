// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player

import io.github.joyelliot.zivplayer.core.model.QueueItemId

enum class StateChangeCause {
    COMMAND,
    BACKEND_EVENT,
    SHUTDOWN,
}

enum class ItemTransitionReason {
    QUEUE_REPLACED,
    SKIP_NEXT,
    SKIP_PREVIOUS,
    AUTOMATIC,
    REPEAT_ONE,
}

sealed interface PlaybackEvent {
    val sequence: Long
    val revision: Long

    data class StateChanged(
        override val sequence: Long,
        override val revision: Long,
        val snapshot: PlaybackSnapshot,
        val cause: StateChangeCause,
    ) : PlaybackEvent

    data class ItemTransition(
        override val sequence: Long,
        override val revision: Long,
        val from: QueueItemId?,
        val to: QueueItemId,
        val reason: ItemTransitionReason,
    ) : PlaybackEvent

    data class ErrorRaised(
        override val sequence: Long,
        override val revision: Long,
        val error: PlayerError,
    ) : PlaybackEvent

    data class SessionClosed(
        override val sequence: Long,
        override val revision: Long,
    ) : PlaybackEvent
}
