// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot
import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MpvTrackObserverTest {
    @Test fun preservesPixelAspectAndNormalizesRotationMetadata() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        fixture.count(1)
        fixture.children().forEach { field ->
            when (field.value.substringAfterLast('/')) {
                "demux-par" -> fixture.emit(field.toPair(), MpvPropertyValue.DoubleValue(4.0 / 3.0))
                "demux-rotation" -> fixture.emit(field.toPair(), MpvPropertyValue.Int64(-90))
                else -> fixture.row(field, type = "video")
            }
        }
        val track = fixture.published.single().second.available.single()
        assertEquals(4f / 3f, track.pixelWidthHeightRatio, 0.0001f)
        assertEquals(270, track.rotationDegrees)
        val ratioField = fixture.children().single { it.value.endsWith("/demux-par") }
        fixture.emit(ratioField.toPair(), MpvPropertyValue.DoubleValue(Double.NaN))
        assertEquals(1f, fixture.published.last().second.available.single().pixelWidthHeightRatio, 0f)
        assertEquals(2, fixture.published.size)
    }

    @Test fun waitsForOptionalUnavailableFieldsAndQualifiesNativeIdsByKind() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        fixture.count(2)
        val fields = fixture.children()
        fields.dropLast(1).forEach { fixture.row(it, type = if (it.value.contains("/0/")) "video" else "audio") }
        assertTrue(fixture.published.isEmpty())
        fixture.row(fields.last(), type = "audio")
        val snapshot = fixture.published.single().second
        assertEquals(listOf("video:1", "audio:1"), snapshot.available.map { it.id.value })
        assertEquals(TrackId("audio:1"), snapshot.selected[TrackKind.AUDIO])
        assertEquals(TrackId("video:1"), snapshot.selected[TrackKind.VIDEO])
    }

    @Test fun sameCountInvalidationDiscardsCopiedOldFieldEvents() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        fixture.count(1)
        val staleFields = fixture.children()
        fixture.parentChanged()
        fixture.count(1)
        staleFields.forEach { fixture.row(it, type = "audio", id = 1) }
        assertTrue(fixture.published.isEmpty())
        fixture.children().forEach { fixture.row(it, type = "sub", id = 7) }
        assertEquals(listOf("sub:7"), fixture.published.single().second.available.map { it.id.value })
    }

    @Test fun switchingMediaRejectsRetiredCountAndFieldTokens() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        val oldCount = fixture.observations.entries.single { it.value == "track-list/count" }.toPair()
        fixture.count(1)
        val staleFields = fixture.children()
        fixture.observer.start(LoadGeneration(2))
        fixture.emit(oldCount, MpvPropertyValue.Int64(100))
        staleFields.forEach { fixture.row(it, type = "audio") }
        assertTrue(fixture.published.isEmpty())
        fixture.count(0)
        assertEquals(listOf(LoadGeneration(2) to TrackSnapshot.EMPTY), fixture.published)
    }

    @Test fun multipleSelectedTracksOfOneKindCannotPublishAnAmbiguousSnapshot() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        fixture.count(2)
        fixture.children().forEach { fixture.row(it, type = "audio", id = if (it.value.contains("/0/")) 1 else 2) }
        assertTrue(fixture.published.isEmpty())
    }

    @Test fun secondarySubtitleIsAvailableButIsNotThePrimarySelection() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        fixture.count(2)
        fixture.children().forEach {
            val secondary = it.value.contains("/1/")
            fixture.row(it, type = "sub", id = if (secondary) 2 else 1, selection = if (secondary) 1 else 0)
        }
        assertEquals(2, fixture.published.single().second.available.size)
        assertEquals(TrackId("sub:1"), fixture.published.single().second.selected[TrackKind.SUBTITLE])
    }

    @Test fun hostileTrackCountCannotCreateAnUnboundedObservationSet() {
        val fixture = Fixture()
        fixture.observer.start(LoadGeneration(1))
        fixture.count(Long.MAX_VALUE)
        assertEquals(listOf("track-list"), fixture.observations.values.toList())
        assertEquals(TrackSnapshot.EMPTY, fixture.published.single().second)
    }

    private class Fixture {
        val observations = linkedMapOf<Long, String>()
        val published = mutableListOf<Pair<LoadGeneration, TrackSnapshot>>()
        val observer = MpvTrackObserver(
            observe = { path, _, token -> observations[token] = path },
            unobserve = { observations.remove(it) },
            publish = { generation, tracks -> published += generation to tracks },
        )

        fun count(count: Long) = emit(
            observations.entries.single { it.value == "track-list/count" }.toPair(),
            MpvPropertyValue.Int64(count),
        )

        fun parentChanged() = emit(
            observations.entries.single { it.value == "track-list" }.toPair(),
            MpvPropertyValue.Unavailable,
        )

        fun children(): List<Map.Entry<Long, String>> = observations.entries
            .filter { it.value.startsWith("track-list/") && it.value != "track-list/count" }

        fun row(field: Map.Entry<Long, String>, type: String, id: Long = 1, selection: Long = 0) {
            val value = when (field.value.substringAfterLast('/')) {
                "id" -> MpvPropertyValue.Int64(id)
                "type" -> MpvPropertyValue.StringValue(type)
                "selected" -> MpvPropertyValue.Flag(true)
                "main-selection" -> MpvPropertyValue.Int64(selection)
                else -> MpvPropertyValue.Unavailable
            }
            emit(field.toPair(), value)
        }

        fun emit(field: Pair<Long, String>, value: MpvPropertyValue) {
            observer.onProperty(MpvPropertyChange(field.second, 0, value, field.first))
        }
    }
}
