// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.core.media.PlayerPreferencesRepository
import io.github.joyelliot.zivplayer.core.media.MediaPlaybackPreferencesRepository
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.PlaybackResourceId
import android.net.Uri

/** Process-level dependency exposed by the application to the playback service. */
interface PlaybackHistoryProvider {
    val recentMediaRepository: RecentMediaRepository
    val playerPreferencesRepository: PlayerPreferencesRepository? get() = null
    val mediaPlaybackPreferencesRepository: MediaPlaybackPreferencesRepository? get() = null
    suspend fun resolvePlaybackResources(options: PlaybackOptions): PlaybackResourceLocations = PlaybackResourceLocations()
    suspend fun importSubtitleResource(uri: Uri): ManagedSubtitleResource = error("Persistent subtitle import is unavailable.")
    suspend fun findSubtitleResource(id: PlaybackResourceId): ManagedSubtitleResource? = null
}

data class PlaybackResourceLocations(val fontsDirectory: String? = null, val shaderFiles: List<String> = emptyList())
data class ManagedSubtitleResource(val id: PlaybackResourceId, val uri: Uri, val title: String)
