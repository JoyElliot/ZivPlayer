// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository

/** Process-level dependency exposed by the application to the playback service. */
interface PlaybackHistoryProvider {
    val recentMediaRepository: RecentMediaRepository
}
