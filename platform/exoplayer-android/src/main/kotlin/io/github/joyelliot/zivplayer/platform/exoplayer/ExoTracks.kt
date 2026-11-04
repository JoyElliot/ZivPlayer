// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.common.C
import androidx.media3.common.TrackGroup
import androidx.media3.common.Tracks
import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.TrackRole
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot

internal data class ExoTrack(val descriptor: TrackDescriptor, val group: TrackGroup, val indices: List<Int>)

internal fun Tracks.toBackendTracks(externalIds: Set<String>): Pair<TrackSnapshot, Map<TrackId, ExoTrack>> {
    val entries = linkedMapOf<TrackId, ExoTrack>()
    val selected = mutableMapOf<TrackKind, TrackId?>()
    val ordinals = mutableMapOf<TrackKind, Int>()
    groups.forEach { group ->
        val kind = when (group.type) {
            C.TRACK_TYPE_AUDIO -> TrackKind.AUDIO
            C.TRACK_TYPE_VIDEO -> TrackKind.VIDEO
            C.TRACK_TYPE_TEXT -> TrackKind.SUBTITLE
            else -> return@forEach
        }
        val ordinal = ordinals.getOrDefault(kind, 0)
        ordinals[kind] = ordinal + 1
        val indices = (0 until group.length).filter { group.isTrackSupported(it) }
        if (indices.isNotEmpty()) {
            // 一个自适应组是一个用户可选轨道，选择时保留组内的码率自适应。
            val index = indices.firstOrNull { group.isTrackSelected(it) } ?: indices.first()
            val format = group.getTrackFormat(index)
            val external = externalIds.firstOrNull { format.id == it || format.id?.endsWith(":$it") == true }
            val id = TrackId(external ?: "${kind.name.lowercase()}:$ordinal")
            val descriptor = TrackDescriptor(
                id = id, kind = kind, languageTag = format.language?.takeIf(String::isNotBlank),
                label = format.label?.takeIf(String::isNotBlank),
                codec = (format.codecs ?: format.sampleMimeType)?.takeIf(String::isNotBlank),
                bitrate = format.bitrate.takeIf { it >= 0 }, channels = format.channelCount.takeIf { it > 0 },
                sampleRateHz = format.sampleRate.takeIf { it > 0 }, width = format.width.takeIf { it > 0 },
                height = format.height.takeIf { it > 0 }, isExternal = external != null,
                pixelWidthHeightRatio = format.pixelWidthHeightRatio.takeIf { it.isFinite() && it > 0 } ?: 1f,
                rotationDegrees = ((format.rotationDegrees % 360) + 360) % 360,
                roles = buildSet {
                    if (format.selectionFlags and C.SELECTION_FLAG_DEFAULT != 0) add(TrackRole.DEFAULT)
                    if (format.selectionFlags and C.SELECTION_FLAG_FORCED != 0) add(TrackRole.FORCED)
                    if (format.roleFlags and C.ROLE_FLAG_COMMENTARY != 0) add(TrackRole.COMMENTARY)
                    if (format.roleFlags and C.ROLE_FLAG_DESCRIBES_MUSIC_AND_SOUND != 0) add(TrackRole.HEARING_IMPAIRED)
                    if (format.roleFlags and C.ROLE_FLAG_DESCRIBES_VIDEO != 0) add(TrackRole.VISUAL_IMPAIRED)
                },
            )
            entries[id] = ExoTrack(descriptor, group.mediaTrackGroup, indices)
            if (group.isSelected) selected[kind] = id
        }
    }
    return TrackSnapshot.of(entries.values.map { it.descriptor }, selected) to entries
}

internal fun TrackKind.media3Type(): Int = when (this) {
    TrackKind.AUDIO -> C.TRACK_TYPE_AUDIO
    TrackKind.VIDEO -> C.TRACK_TYPE_VIDEO
    TrackKind.SUBTITLE -> C.TRACK_TYPE_TEXT
}
