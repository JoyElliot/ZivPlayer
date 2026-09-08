// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.TrackRole
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot
import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration

/**
 * Collects the public primitive track-list subproperties using the existing JNI
 * contract. A parent NONE observation invalidates even same-count replacements.
 * Every refresh gets fresh tokens; copied events from retired observations are
 * ignored. mpv observations are asynchronous, so this is a converging snapshot,
 * not an atomic native read. No partial initial field set is published.
 * All methods are called under the backend's native gate.
 */
internal class MpvTrackObserver(
    private val observe: (String, MpvPropertyFormat, Long) -> Unit,
    private val unobserve: (Long) -> Unit,
    private val publish: (LoadGeneration, TrackSnapshot) -> Unit,
) {
    private var nextToken = 100L
    private var generation: LoadGeneration? = null
    private var parentToken: Long? = null
    private var countToken: Long? = null
    private val fields = linkedMapOf<Long, Field>()
    private val values = mutableMapOf<Long, MpvPropertyValue>()
    private var count = 0
    private var lastPublished: TrackSnapshot? = null

    fun start(generation: LoadGeneration) {
        reset()
        this.generation = generation
        parentToken = token().also { observe("track-list", MpvPropertyFormat.NONE, it) }
        refresh()
    }

    fun reset() {
        generation = null
        parentToken?.let(unobserve)
        parentToken = null
        clearFields()
        lastPublished = null
    }

    fun onProperty(change: MpvPropertyChange): Boolean {
        val observedToken = change.replyUserdata ?: return false
        if (observedToken < FIRST_TOKEN) return false
        if (generation == null) return true
        when (observedToken) {
            parentToken -> if (change.name == "track-list") refresh()
            countToken -> {
                if (change.name != "track-list/count") return true
                val nativeCount = (change.value as? MpvPropertyValue.Int64)?.value
                if (nativeCount == null || nativeCount !in 0L..MAX_TRACKS.toLong()) {
                    clearFields()
                    publishSnapshot(TrackSnapshot.EMPTY)
                    return true
                }
                unobserve(observedToken)
                countToken = null
                count = nativeCount.toInt()
                for (index in 0 until count) {
                    for ((name, format) in FIELD_FORMATS) {
                        val fieldToken = token()
                        val path = "track-list/$index/$name"
                        fields[fieldToken] = Field(index, name, path)
                        observe(path, format, fieldToken)
                    }
                }
                if (count == 0) publishSnapshot(TrackSnapshot.EMPTY)
            }
            else -> {
                val field = fields[observedToken] ?: return true
                if (change.name != field.path) return true
                values[observedToken] = change.value
                if (values.size == fields.size) buildSnapshot()?.let(::publishSnapshot)
            }
        }
        return true
    }

    private fun refresh() {
        clearFields()
        countToken = token().also { observe("track-list/count", MpvPropertyFormat.INT64, it) }
    }

    private fun clearFields() {
        countToken?.let(unobserve)
        countToken = null
        fields.keys.forEach(unobserve)
        fields.clear()
        values.clear()
        count = 0
    }

    private fun buildSnapshot(): TrackSnapshot? {
        val rows = List(count) { mutableMapOf<String, MpvPropertyValue>() }
        fields.forEach { (token, field) -> rows[field.index][field.name] = values.getValue(token) }
        val available = mutableListOf<TrackDescriptor>()
        val selected = TrackKind.entries.associateWith<TrackKind, TrackId?> { null }.toMutableMap()
        for (row in rows) {
            val kind = when (row.string("type")) {
                "audio" -> TrackKind.AUDIO
                "video" -> TrackKind.VIDEO
                "sub" -> TrackKind.SUBTITLE
                else -> return null
            }
            val nativeId = (row["id"] as? MpvPropertyValue.Int64)?.value
                ?.takeIf { it > 0 } ?: return null
            val id = TrackId("${kind.mpvTrackPrefix()}:$nativeId")
            if (available.any { it.id == id }) return null
            val isSelected = (row["selected"] as? MpvPropertyValue.Flag)?.value ?: return null
            if (isSelected) {
                // Secondary subtitles are outside the first UI's single-selection model.
                val mainSelection = row.positiveOrZero("main-selection")
                if (mainSelection == null || mainSelection == 0) {
                    if (selected[kind] != null) return null
                    selected[kind] = id
                }
            }
            available += TrackDescriptor(
                id = id, kind = kind,
                languageTag = row.string("lang"), label = row.string("title"), codec = row.string("codec"),
                bitrate = row.positiveOrZero("demux-bitrate"),
                channels = row.positive("demux-channel-count"), sampleRateHz = row.positive("demux-samplerate"),
                width = row.positive("demux-w"), height = row.positive("demux-h"),
                pixelWidthHeightRatio = (row["demux-par"] as? MpvPropertyValue.DoubleValue)?.value?.toFloat()
                    ?.takeIf { it.isFinite() && it > 0f } ?: 1f,
                rotationDegrees = (row["demux-rotation"] as? MpvPropertyValue.Int64)?.value
                    ?.let { ((it % 360 + 360) % 360).toInt() } ?: 0,
                isExternal = row.flag("external"),
                roles = buildSet {
                    ROLE_FIELDS.forEach { (name, role) -> if (row.flag(name)) add(role) }
                },
            )
        }
        return TrackSnapshot.of(available, selected)
    }

    private fun publishSnapshot(snapshot: TrackSnapshot) {
        val active = generation ?: return
        if (snapshot != lastPublished) {
            lastPublished = snapshot
            publish(active, snapshot)
        }
    }

    private fun token(): Long = nextToken.also {
        check(it < Long.MAX_VALUE) { "Track observation tokens exhausted." }
        nextToken++
    }

    private data class Field(val index: Int, val name: String, val path: String)

    private companion object {
        const val FIRST_TOKEN = 100L
        const val MAX_TRACKS = 128
        val ROLE_FIELDS = mapOf(
            "default" to TrackRole.DEFAULT, "forced" to TrackRole.FORCED,
            "commentary" to TrackRole.COMMENTARY, "hearing-impaired" to TrackRole.HEARING_IMPAIRED,
            "visual-impaired" to TrackRole.VISUAL_IMPAIRED,
        )
        val FIELD_FORMATS = buildMap {
            listOf("id", "main-selection", "demux-bitrate", "demux-channel-count", "demux-samplerate", "demux-w", "demux-h", "demux-rotation")
                .forEach { put(it, MpvPropertyFormat.INT64) }
            put("demux-par", MpvPropertyFormat.DOUBLE)
            listOf("type", "title", "lang", "codec").forEach { put(it, MpvPropertyFormat.STRING) }
            (listOf("selected", "external") + ROLE_FIELDS.keys).forEach { put(it, MpvPropertyFormat.FLAG) }
        }
    }
}

internal fun TrackKind.mpvTrackPrefix(): String = when (this) {
    TrackKind.AUDIO -> "audio"
    TrackKind.VIDEO -> "video"
    TrackKind.SUBTITLE -> "sub"
}

private fun Map<String, MpvPropertyValue>.string(name: String): String? =
    (get(name) as? MpvPropertyValue.StringValue)?.value?.takeIf(String::isNotBlank)

private fun Map<String, MpvPropertyValue>.flag(name: String): Boolean =
    (get(name) as? MpvPropertyValue.Flag)?.value == true

private fun Map<String, MpvPropertyValue>.positiveOrZero(name: String): Int? =
    (get(name) as? MpvPropertyValue.Int64)?.value?.takeIf { it in 0L..Int.MAX_VALUE }?.toInt()

private fun Map<String, MpvPropertyValue>.positive(name: String): Int? =
    positiveOrZero(name)?.takeIf { it > 0 }
