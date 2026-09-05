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
        it.codec == codec && it.isExternal == external }
    return when (candidates.size) {
        0 -> null
        1 -> candidates.single().id
        // External imports can be enumerated in a different order after restart.
        else -> if (external) null else sameKind.getOrNull(index)?.takeIf { it in candidates }?.id
    }
}
