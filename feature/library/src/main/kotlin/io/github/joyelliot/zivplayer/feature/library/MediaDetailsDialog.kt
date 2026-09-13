// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.feature.library

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.core.media.MediaDetails
import io.github.joyelliot.zivplayer.designsystem.*

@Composable
fun MediaDetailsDialog(fileName: String?, details: MediaDetails?, loading: Boolean, onDismiss: () -> Unit) {
    ZivLibraryDialog(stringResource(R.string.library_details), onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            details?.artworkLocator?.let { ZivArtwork(it, Modifier.size(168.dp)
                .align(Alignment.CenterHorizontally).clip(RoundedCornerShape(16.dp))) }
            if (loading) ZivLoadingIndicator()
            DetailField(R.string.library_filename, details?.fileName ?: fileName)
            DetailField(R.string.library_metadata_title, details?.title)
            DetailField(R.string.library_artist, details?.artist)
            DetailField(R.string.library_album, details?.album)
            DetailField(R.string.library_album_artist, details?.albumArtist)
            DetailField(R.string.library_year, details?.year)
            DetailField(R.string.library_genre, details?.genre)
            DetailField(R.string.library_track, details?.trackNumber)
            DetailField(R.string.library_disc, details?.discNumber)
            DetailField(R.string.library_duration, details?.durationMs?.let(::formatMediaDuration))
            DetailField(R.string.library_size, details?.sizeBytes?.let(::formatLibrarySize))
            DetailField(R.string.library_format, details?.mimeType)
            DetailField(R.string.library_resolution, details?.let { value ->
                val w = value.videoWidth; val h = value.videoHeight
                if (w != null && h != null) "$w × $h" else null
            })
            DetailField(R.string.library_rotation, details?.rotationDegrees?.takeIf { it != 0 }?.let { "$it°" })
            DetailField(R.string.library_video_codec, details?.videoCodec)
            DetailField(R.string.library_audio_codec, details?.audioCodec)
            DetailField(R.string.library_bitrate, details?.bitrate?.let { "${it / 1000} kb/s" })
            DetailField(R.string.library_sample_rate, details?.sampleRate?.let { "$it Hz" })
            DetailField(R.string.library_channels, details?.channelCount?.toString())
            if (!loading) ZivLibraryText(stringResource(R.string.library_metadata_optional), secondary = true)
        }
    }
}

@Composable
private fun DetailField(label: Int, value: String?) {
    if (value.isNullOrBlank()) return
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        ZivLibraryText(stringResource(label), secondary = true, maxLines = 1)
        ZivLibraryText(value, maxLines = 5)
    }
}

internal fun formatMediaDuration(ms: Long): String {
    val seconds = (ms / 1000).coerceAtLeast(0)
    val minutes = seconds / 60
    return if (minutes >= 60) "${minutes / 60}:${(minutes % 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}"
    else "$minutes:${(seconds % 60).toString().padStart(2, '0')}"
}
