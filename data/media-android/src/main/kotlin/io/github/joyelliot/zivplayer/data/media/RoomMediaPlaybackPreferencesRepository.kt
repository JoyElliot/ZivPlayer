// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.withTransaction
import io.github.joyelliot.zivplayer.core.media.MediaPlaybackPreferences
import io.github.joyelliot.zivplayer.core.media.MediaPlaybackPreferencesRepository
import io.github.joyelliot.zivplayer.core.media.SavedSubtitle
import io.github.joyelliot.zivplayer.core.media.SavedTrackSelection
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.PlaybackResourceId
import io.github.joyelliot.zivplayer.core.model.TrackKind

class RoomMediaPlaybackPreferencesRepository internal constructor(private val database: ZivMediaDatabase) :
    MediaPlaybackPreferencesRepository {
    private val dao = database.playbackPreferencesDao()
    override suspend fun find(mediaId: MediaId): MediaPlaybackPreferences = database.withTransaction {
        val choices = dao.choices(mediaId.value)
        MediaPlaybackPreferences(
            audio = choices.find { it.kind == TrackKind.AUDIO.name }?.selection(),
            subtitle = choices.find { it.kind == TrackKind.SUBTITLE.name }?.selection(),
            subtitlesDisabled = choices.find { it.kind == TrackKind.SUBTITLE.name }?.disabled == true,
            externalSubtitles = dao.subtitles(mediaId.value).map {
                SavedSubtitle(PlaybackResourceId(it.resourceId), it.title)
            },
        )
    }

    override suspend fun saveTrack(mediaId: MediaId, selection: SavedTrackSelection?, kind: TrackKind): Boolean =
        database.withTransaction {
            require(kind == TrackKind.AUDIO || kind == TrackKind.SUBTITLE)
            require(selection == null || selection.kind == kind)
            require(selection != null || kind == TrackKind.SUBTITLE)
            if (database.recentMediaDao().findById(mediaId.value) == null) return@withTransaction false
            dao.upsertChoice(TrackChoiceEntity(mediaId.value, kind.name, selection?.index,
                selection?.language, selection?.label, selection?.codec, selection?.external ?: false,
                disabled = selection == null))
            true
        }

    override suspend fun saveSubtitle(mediaId: MediaId, subtitle: SavedSubtitle): Boolean = database.withTransaction {
        if (database.recentMediaDao().findById(mediaId.value) == null) return@withTransaction false
        val existing = dao.subtitles(mediaId.value)
        require(existing.size < 16 || existing.any { it.resourceId == subtitle.resourceId.value }) {
            "每个媒体最多保存 16 个外置字幕"
        }
        dao.upsertSubtitle(ExternalSubtitleEntity(mediaId.value, subtitle.resourceId.value, subtitle.title))
        // Adding a subtitle explicitly enables it; an older persisted Off must not win on reopen.
        dao.deleteChoice(mediaId.value, TrackKind.SUBTITLE.name)
        true
    }

    override suspend fun clear(mediaId: MediaId) = database.withTransaction {
        dao.clearChoices(mediaId.value)
        dao.clearSubtitles(mediaId.value)
    }
}
