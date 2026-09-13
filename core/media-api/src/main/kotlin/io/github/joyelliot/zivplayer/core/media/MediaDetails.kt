// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import kotlinx.coroutines.flow.StateFlow

/** Optional, derived file information. Missing metadata stays unknown rather than becoming zero. */
data class MediaDetails(
    val fileName: String? = null,
    val mimeType: String? = null,
    val sizeBytes: Long? = null,
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    val albumArtist: String? = null,
    val year: String? = null,
    val genre: String? = null,
    val trackNumber: String? = null,
    val discNumber: String? = null,
    val durationMs: Long? = null,
    val videoWidth: Int? = null,
    val videoHeight: Int? = null,
    val rotationDegrees: Int? = null,
    val bitrate: Long? = null,
    val videoCodec: String? = null,
    val audioCodec: String? = null,
    val sampleRate: Int? = null,
    val channelCount: Int? = null,
    val artworkLocator: String? = null,
) {
    val playbackMetadata: MediaMetadata get() = MediaMetadata(title ?: fileName, artist, album, artworkLocator)
}

data class MediaDetailsRequest(
    val sourceUri: DurableMediaUri,
    val fileName: String? = null,
    val mimeType: String? = null,
    val sizeBytes: Long? = null,
    val modifiedEpochMs: Long? = null,
)

/** Cache reads never register history, change checkpoints, or acquire additional source grants. */
interface MediaDetailsRepository {
    val details: StateFlow<Map<String, MediaDetails>>
    suspend fun load(request: MediaDetailsRequest): MediaDetails?
}
