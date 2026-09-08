// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.playback

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.TrackGroup
import androidx.media3.common.Tracks
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.TrackSelectionParameters
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.TrackRole
import io.github.joyelliot.zivplayer.core.player.PlayerCommand
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot

internal fun TrackDescriptor.toMedia3Format(): Format {
    val track = this
    return Format.Builder()
        .setId(track.id.value)
        .setLabel(track.label)
        .setLanguage(track.languageTag)
        .setCodecs(track.codec)
        .setSampleMimeType(when (track.kind) {
            TrackKind.AUDIO -> MimeTypes.AUDIO_UNKNOWN
            TrackKind.VIDEO -> MimeTypes.VIDEO_UNKNOWN
            TrackKind.SUBTITLE -> MimeTypes.TEXT_UNKNOWN
        })
        .setAverageBitrate(track.bitrate ?: Format.NO_VALUE)
        .setChannelCount(track.channels ?: Format.NO_VALUE)
        .setSampleRate(track.sampleRateHz ?: Format.NO_VALUE)
        .setWidth(track.width ?: Format.NO_VALUE)
        .setHeight(track.height ?: Format.NO_VALUE)
        .setPixelWidthHeightRatio(track.pixelWidthHeightRatio)
        .setRotationDegrees(track.rotationDegrees)
        .setSelectionFlags(
            (if (TrackRole.DEFAULT in track.roles) C.SELECTION_FLAG_DEFAULT else 0) or
                (if (TrackRole.FORCED in track.roles) C.SELECTION_FLAG_FORCED else 0),
        )
        .build()
}

/** Queue occurrence identity prevents an old controller override selecting a new file's same ID. */
internal fun TrackSnapshot.toMedia3Tracks(queueItemId: String): Tracks = Tracks(available.map { track ->
    val format = track.toMedia3Format()
    Tracks.Group(
        TrackGroup("ziv:$queueItemId:${track.id.value}", format), false,
        intArrayOf(C.FORMAT_HANDLED), booleanArrayOf(selected[track.kind] == track.id),
    )
})

/** Rebuild from observed core state so imports and partial command failures cannot leave stale overrides. */
internal fun TrackSnapshot.toMedia3SelectionParameters(queueItemId: String): TrackSelectionParameters {
    val groups = toMedia3Tracks(queueItemId).groups
    return TrackSelectionParameters.DEFAULT.buildUpon().apply {
        groups.filter { it.type in SELECTABLE_TYPES && it.isSelected }.forEach { group ->
            setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0))
        }
        setTrackTypeDisabled(C.TRACK_TYPE_TEXT,
            groups.any { it.type == C.TRACK_TYPE_TEXT } && groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected })
    }.build()
}

internal fun TrackSnapshot.selectionCommands(
    queueItemId: String,
    parameters: TrackSelectionParameters,
): List<PlayerCommand.SelectTrack> {
    require(parameters.buildUpon().clearOverrides().setDisabledTrackTypes(emptySet()).build() ==
        TrackSelectionParameters.DEFAULT) { "Use an explicit audio or subtitle track selection." }
    require(parameters.disabledTrackTypes.all { it == C.TRACK_TYPE_TEXT } &&
        parameters.overrides.values.all { it.type in SELECTABLE_TYPES }) { "Only audio/subtitle selection and subtitles off are supported." }
    val groups = toMedia3Tracks(queueItemId).groups
    return listOf(TrackKind.AUDIO, TrackKind.SUBTITLE).mapNotNull { kind ->
        val type = kind.toMedia3TrackType()
        val overrides = parameters.overrides.values.filter { it.type == type }
        require(overrides.size <= 1) { "Only one track per type may be selected." }
        val selectionOverride = overrides.singleOrNull()
        if (type in parameters.disabledTrackTypes || selectionOverride?.trackIndices?.isEmpty() == true) {
            require(kind == TrackKind.SUBTITLE) { "Only subtitle output may be disabled." }
            PlayerCommand.SelectTrack(kind, null)
        } else if (selectionOverride != null) {
            val group = groups.singleOrNull { it.mediaTrackGroup == selectionOverride.mediaTrackGroup }
                ?: error("The selected track belongs to a different media item or track list.")
            require(selectionOverride.trackIndices == listOf(0)) { "The requested track index is unavailable." }
            PlayerCommand.SelectTrack(kind, TrackId(checkNotNull(group.getTrackFormat(0).id)))
        } else {
            require(selected[kind] == null) { "Automatic track reset is unavailable; choose a track or turn subtitles off." }
            null
        }
    }
}

private val SELECTABLE_TYPES = setOf(C.TRACK_TYPE_AUDIO, C.TRACK_TYPE_TEXT)

internal fun TrackKind.toMedia3TrackType(): Int = when (this) {
    TrackKind.AUDIO -> C.TRACK_TYPE_AUDIO
    TrackKind.VIDEO -> C.TRACK_TYPE_VIDEO
    TrackKind.SUBTITLE -> C.TRACK_TYPE_TEXT
}
