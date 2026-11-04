// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.TrackGroup
import androidx.media3.common.Tracks
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import org.junit.Assert.*
import org.junit.Test

class ExoTracksTest {
    @Test fun adaptiveVariantsRemainOneSelectableTrack() {
        val formats = listOf(128000, 256000).map { bitrate ->
            Format.Builder().setSampleMimeType("audio/mp4a-latm").setAverageBitrate(bitrate).setLanguage("en").build()
        }
        val group = TrackGroup("audio", *formats.toTypedArray())
        val tracks = Tracks(listOf(Tracks.Group(group, true, intArrayOf(C.FORMAT_HANDLED, C.FORMAT_HANDLED), booleanArrayOf(true, true))))
        val (snapshot, mapping) = tracks.toBackendTracks(emptySet())
        assertEquals(1, mapping.size)
        val entry = mapping.values.single()
        assertEquals(listOf(0, 1), entry.indices)
        assertEquals(entry.descriptor.id, snapshot.selected[TrackKind.AUDIO])
    }

    @Test fun externalIdsSurviveMediaSourcePrefixesAndUnsupportedGroups() {
        val unsupported = TrackGroup("unsupported", Format.Builder().setSampleMimeType("text/x-ssa").build())
        val external = TrackGroup("subtitle", Format.Builder().setId("1:external:0").setSampleMimeType("application/x-subrip").build())
        val tracks = Tracks(listOf(
            Tracks.Group(unsupported, false, intArrayOf(C.FORMAT_UNSUPPORTED_TYPE), booleanArrayOf(false)),
            Tracks.Group(external, false, intArrayOf(C.FORMAT_HANDLED), booleanArrayOf(true)),
        ))
        val (_, mapping) = tracks.toBackendTracks(setOf("external:0"))
        assertEquals(setOf(TrackId("external:0")), mapping.keys)
        assertTrue(mapping.values.single().descriptor.isExternal)
    }
}
