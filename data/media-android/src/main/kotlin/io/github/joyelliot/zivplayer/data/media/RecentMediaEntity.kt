// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.MediaRecord
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.media.RecentMedia
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import io.github.joyelliot.zivplayer.core.model.Milliseconds

@Entity(
    tableName = "recent_media",
    indices = [Index(value = ["source_uri"], unique = true)],
)
internal data class RecentMediaEntity(
    @PrimaryKey
    @ColumnInfo(name = "media_id")
    val mediaId: String,
    @ColumnInfo(name = "source_uri")
    val sourceUri: String,
    @ColumnInfo(name = "mime_type")
    val mimeType: String?,
    val title: String?,
    val artist: String?,
    val album: String?,
    @ColumnInfo(name = "artwork_uri")
    val artworkUri: String?,
    @ColumnInfo(name = "added_at_epoch_ms")
    val addedAtEpochMs: Long,
    @ColumnInfo(name = "last_opened_at_epoch_ms")
    val lastOpenedAtEpochMs: Long,
    @ColumnInfo(name = "checkpoint_position_ms")
    val checkpointPositionMs: Long?,
    @ColumnInfo(name = "checkpoint_duration_ms")
    val checkpointDurationMs: Long?,
    @ColumnInfo(name = "checkpoint_completed")
    val checkpointCompleted: Boolean?,
    @ColumnInfo(name = "checkpoint_updated_at_epoch_ms")
    val checkpointUpdatedAtEpochMs: Long?,
    @ColumnInfo(name = "visible_in_history", defaultValue = "1")
    val visibleInHistory: Boolean = true,
)

internal fun MediaRegistration.toNewEntity(id: MediaId): RecentMediaEntity = RecentMediaEntity(
    mediaId = id.value,
    sourceUri = sourceUri.value,
    mimeType = mimeType,
    title = metadata.title,
    artist = metadata.artist,
    album = metadata.album,
    artworkUri = metadata.artworkLocator,
    addedAtEpochMs = openedAt.value,
    lastOpenedAtEpochMs = openedAt.value,
    checkpointPositionMs = null,
    checkpointDurationMs = null,
    checkpointCompleted = null,
    checkpointUpdatedAtEpochMs = null,
    visibleInHistory = visibleInHistory,
)

internal fun RecentMediaEntity.merge(registration: MediaRegistration): RecentMediaEntity {
    require(sourceUri == registration.sourceUri.value) {
        "A media record cannot be merged with a different source URI."
    }
    return copy(
        mimeType = registration.mimeType ?: mimeType,
        title = registration.metadata.title ?: title,
        artist = registration.metadata.artist ?: artist,
        album = registration.metadata.album ?: album,
        artworkUri = registration.metadata.artworkLocator ?: artworkUri,
        lastOpenedAtEpochMs = if (registration.visibleInHistory) registration.openedAt.value else lastOpenedAtEpochMs,
        visibleInHistory = visibleInHistory || registration.visibleInHistory,
    )
}

internal fun RecentMediaEntity.withCheckpoint(
    checkpoint: PlaybackCheckpoint,
): RecentMediaEntity {
    require(mediaId == checkpoint.mediaId.value) {
        "A checkpoint cannot be attached to a different media record."
    }
    if (
        checkpointUpdatedAtEpochMs != null &&
        checkpoint.updatedAt.value < checkpointUpdatedAtEpochMs
    ) {
        return this
    }
    return copy(
        checkpointPositionMs = checkpoint.position.value,
        checkpointDurationMs = checkpoint.duration?.value,
        checkpointCompleted = checkpoint.completed,
        checkpointUpdatedAtEpochMs = checkpoint.updatedAt.value,
    )
}

internal fun RecentMediaEntity.toRecentMedia(): RecentMedia {
    val id = MediaId(mediaId)
    val checkpoint = checkpointUpdatedAtEpochMs?.let { updatedAt ->
        PlaybackCheckpoint(
            mediaId = id,
            position = Milliseconds(
                checkNotNull(checkpointPositionMs) { "Stored checkpoint position is missing." },
            ),
            duration = checkpointDurationMs?.let(::Milliseconds),
            completed = checkNotNull(checkpointCompleted) {
                "Stored checkpoint completion state is missing."
            },
            updatedAt = EpochMilliseconds(updatedAt),
        )
    }
    return RecentMedia(
        record = MediaRecord(
            id = id,
            sourceUri = DurableMediaUri(sourceUri),
            mimeType = mimeType,
            metadata = MediaMetadata(
                title = title,
                artist = artist,
                album = album,
                artworkLocator = artworkUri,
            ),
            addedAt = EpochMilliseconds(addedAtEpochMs),
            lastOpenedAt = EpochMilliseconds(lastOpenedAtEpochMs),
        ),
        checkpoint = checkpoint,
    )
}
