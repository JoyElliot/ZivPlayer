// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import androidx.media3.common.Player
import io.github.joyelliot.zivplayer.core.player.PlaybackSnapshot
import io.github.joyelliot.zivplayer.core.player.PlayerStatus

internal data class PlaybackStateProjection(
    @Player.State val playbackState: Int,
    val isLoading: Boolean,
    val exposesError: Boolean,
)

internal fun PlaybackSnapshot.toMedia3Projection(): PlaybackStateProjection = when (status) {
    PlayerStatus.IDLE -> PlaybackStateProjection(Player.STATE_IDLE, false, false)
    PlayerStatus.LOADING -> PlaybackStateProjection(Player.STATE_BUFFERING, true, false)
    PlayerStatus.READY,
    PlayerStatus.PLAYING,
    PlayerStatus.PAUSED,
    -> PlaybackStateProjection(Player.STATE_READY, false, false)

    PlayerStatus.BUFFERING -> PlaybackStateProjection(Player.STATE_BUFFERING, true, false)
    PlayerStatus.ENDED -> PlaybackStateProjection(Player.STATE_ENDED, false, false)
    PlayerStatus.ERROR -> PlaybackStateProjection(Player.STATE_IDLE, false, true)
    PlayerStatus.CLOSED -> PlaybackStateProjection(Player.STATE_IDLE, false, false)
}
