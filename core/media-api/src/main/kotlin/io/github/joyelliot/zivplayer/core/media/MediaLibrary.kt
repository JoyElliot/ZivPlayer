// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.PlaybackResourceId
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import io.github.joyelliot.zivplayer.core.model.TrackKind
import kotlinx.coroutines.flow.Flow

enum class LibraryFolderStatus { NEVER_SCANNED, SCANNING, READY, INTERRUPTED, ACCESS_LOST, FAILED }

data class LibraryFolder(
    val id: String,
    val sourceUri: DurableMediaUri,
    val name: String,
    val status: LibraryFolderStatus,
    val lastScanEpochMs: Long?,
    val mediaCount: Int,
    val message: String? = null,
)

/** Folder membership is independent of history; scanning never changes last-opened timestamps. */
data class LibraryMedia(
    val folderId: String,
    val sourceUri: DurableMediaUri,
    val title: String,
    val mimeType: String?,
    val relativePath: String,
    val sizeBytes: Long?,
    val modifiedEpochMs: Long?,
    val favorite: Boolean = false,
    val hidden: Boolean = false,
)

interface MediaLibraryRepository {
    fun observeFolders(): Flow<List<LibraryFolder>>
    fun observeMedia(): Flow<List<LibraryMedia>>
    suspend fun setFavorite(folderId: String, sourceUri: DurableMediaUri, favorite: Boolean)
    suspend fun setHidden(folderId: String, sourceUri: DurableMediaUri, hidden: Boolean)
}

interface PlayerPreferencesRepository {
    val preferences: Flow<PlayerPreferences>
    suspend fun update(transform: (PlayerPreferences) -> PlayerPreferences)
}

/** A best-effort media-local fingerprint, never a queue/Media3 group/native track identifier. */
data class SavedTrackSelection(
    val kind: TrackKind,
    val index: Int,
    val language: String? = null,
    val label: String? = null,
    val codec: String? = null,
    val external: Boolean = false,
) {
    init { require(index >= 0) }
}

data class SavedSubtitle(val resourceId: PlaybackResourceId, val title: String)

data class MediaPlaybackPreferences(
    val audio: SavedTrackSelection? = null,
    val subtitle: SavedTrackSelection? = null,
    val subtitlesDisabled: Boolean = false,
    val externalSubtitles: List<SavedSubtitle> = emptyList(),
)

interface MediaPlaybackPreferencesRepository {
    suspend fun find(mediaId: MediaId): MediaPlaybackPreferences
    /** False means history was removed; a late playback callback must not resurrect it. */
    suspend fun saveTrack(mediaId: MediaId, selection: SavedTrackSelection?, kind: TrackKind): Boolean
    suspend fun saveSubtitle(mediaId: MediaId, subtitle: SavedSubtitle): Boolean
    suspend fun clear(mediaId: MediaId)
}

enum class PlaybackResourceKind { FONT, SHADER, SUBTITLE }

data class PlaybackResource(
    val id: PlaybackResourceId,
    val kind: PlaybackResourceKind,
    val title: String,
    val extension: String,
    val sizeBytes: Long,
    val fontFamily: String? = null,
)
