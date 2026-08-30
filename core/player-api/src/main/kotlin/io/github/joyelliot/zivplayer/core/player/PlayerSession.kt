// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

interface PlayerSession {
    val snapshot: StateFlow<PlaybackSnapshot>
    val events: Flow<PlaybackEvent>

    suspend fun dispatch(command: PlayerCommand): CommandResult

    suspend fun close()
}
