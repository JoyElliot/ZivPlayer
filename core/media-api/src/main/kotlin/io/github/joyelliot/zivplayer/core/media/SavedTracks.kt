// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId

fun List<TrackDescriptor>.savedSelection(id: TrackId): SavedTrackSelection? {
    val track = firstOrNull { it.id == id } ?: return null
    return SavedTrackSelection(track.kind, filter { it.kind == track.kind }.indexOf(track),
        track.languageTag, track.label, track.codec, track.isExternal)
}

/** A changed ID is expected. A changed fingerprint is never silently replaced by an ordinal. */
fun SavedTrackSelection.matchTrack(tracks: List<TrackDescriptor>): TrackId? {
    val sameKind = tracks.filter { it.kind == kind }
    val candidates = sameKind.filter { it.languageTag == language && it.label == label &&
        codecFamily(it.codec) == codecFamily(codec) && it.isExternal == external }
    return when (candidates.size) {
        0 -> null
        1 -> candidates.single().id
        // External imports can be enumerated in a different order after restart.
        else -> if (external) null else sameKind.getOrNull(index)?.takeIf { it in candidates }?.id
    }
}

/** 保持旧 mpv 指纹可用，只统一已知编码名；未知格式仍须精确匹配。 */
private fun codecFamily(codec: String?): String? = codec?.lowercase()?.let {
    when {
        it.startsWith("mp4a.40.") || it in setOf("mp4a.66", "mp4a.67", "mp4a.68") || it in setOf("aac", "audio/mp4a-latm") -> "aac"
        it in setOf("ass", "ssa", "text/x-ssa") -> "ass"
        it in setOf("subrip", "srt", "application/x-subrip") -> "subrip"
        it in setOf("webvtt", "text/vtt") -> "webvtt"
        it in setOf("ttml", "application/ttml+xml") -> "ttml"
        it in setOf("opus", "audio/opus") -> "opus"
        it in setOf("flac", "audio/flac") -> "flac"
        it in setOf("vorbis", "audio/vorbis") -> "vorbis"
        it in setOf("mp3", "audio/mpeg") -> "mp3"
        it in setOf("ac3", "ac-3", "audio/ac3") -> "ac3"
        it in setOf("eac3", "ec-3", "audio/eac3", "audio/eac3-joc") -> "eac3"
        it in setOf("hdmv_pgs_subtitle", "application/pgs") -> "pgs"
        it in setOf("dvd_subtitle", "application/vobsub") -> "vobsub"
        else -> it
    }
}
