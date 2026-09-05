// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.core.media

import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull

class SavedTracksTest {
    private fun track(id: String, language: String = "en", external: Boolean = false) =
        TrackDescriptor(TrackId(id), TrackKind.SUBTITLE, languageTag = language, codec = "ass", isExternal = external)

    @Test fun `fingerprint follows a changed native identifier and order`() {
        val saved = listOf(track("old", "zh"), track("other")).savedSelection(TrackId("old"))!!
        assertEquals(TrackId("new"), saved.matchTrack(listOf(track("other"), track("new", "zh"))))
        assertNull(saved.matchTrack(listOf(track("same-index-but-wrong-language"))))
    }

    @Test fun `embedded duplicates use kind local ordinal but external duplicates remain ambiguous`() {
        val embedded = listOf(track("a"), track("b")).savedSelection(TrackId("b"))!!
        assertEquals(TrackId("d"), embedded.matchTrack(listOf(track("c"), track("d"))))
        val external = listOf(track("a", external = true)).savedSelection(TrackId("a"))!!
        assertNull(external.matchTrack(listOf(track("b", external = true), track("c", external = true))))
    }
}
