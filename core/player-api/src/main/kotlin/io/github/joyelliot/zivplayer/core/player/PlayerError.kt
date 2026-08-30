// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player

enum class PlayerErrorKind {
    INVALID_COMMAND,
    UNSUPPORTED_OPERATION,
    SOURCE_UNAVAILABLE,
    BACKEND_OPERATION_FAILED,
    COMMAND_QUEUE_FULL,
    SESSION_CLOSED,
}

enum class ErrorRecovery {
    NONE,
    RETRY,
    SKIP,
    RESET,
}

data class PlayerError(
    val kind: PlayerErrorKind,
    val message: String,
    val recovery: ErrorRecovery = ErrorRecovery.NONE,
) {
    init {
        require(message.isNotBlank()) { "Player error message must not be blank." }
    }
}
