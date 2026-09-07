// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.media.RecentMedia
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.data.media.MediaDocumentForgetResult
import io.github.joyelliot.zivplayer.data.media.MediaDocumentPersistence
import io.github.joyelliot.zivplayer.data.media.OpenedMediaDocument
import io.github.joyelliot.zivplayer.feature.player.RecentMediaUiItem
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.ensureActive
import java.util.UUID

data class PendingMediaPlayback(
    val requestId: Long,
    val dispatchToken: String,
    val document: OpenedMediaDocument,
    val startPositionMs: Long,
    val documents: List<OpenedMediaDocument> = listOf(document),
    val startIndex: Int = 0,
)

enum class MediaSelectionNotice {
    SESSION_ONLY_PERMISSION,
    SESSION_ONLY_HISTORY,
    OPEN_FAILED,
    QUEUE_TOO_LARGE,
    RECENT_ACCESS_LOST,
    HISTORY_UNAVAILABLE,
    HISTORY_FORGOTTEN,
    HISTORY_FORGOTTEN_WITH_ORPHANED_GRANT,
    HISTORY_FORGET_FAILED,
}

/** Retains document registration, recent history and pending playback across recreation. */
class MediaSelectionViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val dependencies = application as ZivPlayerApplication
    private val repository = dependencies.recentMediaRepository

    private val mutablePendingPlayback = mutableStateOf<PendingMediaPlayback?>(null)
    val pendingPlayback: State<PendingMediaPlayback?> = mutablePendingPlayback

    private val mutableRecentMedia = mutableStateOf<List<RecentMediaUiItem>>(emptyList())
    val recentMedia: State<List<RecentMediaUiItem>> = mutableRecentMedia

    private val mutableNotice = mutableStateOf<MediaSelectionNotice?>(null)
    val notice: State<MediaSelectionNotice?> = mutableNotice

    private val mutableHistoryUnavailable = mutableStateOf(false)
    val historyUnavailable: State<Boolean> = mutableHistoryUnavailable

    private var latestRequestId = 0L
    private var playbackRequestId: Long? = null
    private var mediaOperationJob: Job? = null

    init {
        viewModelScope.launch {
            repository.observeRecentlyOpened(RECENT_MEDIA_LIMIT)
                .catch {
                    mutableRecentMedia.value = emptyList()
                    mutableHistoryUnavailable.value = true
                }
                .collect { recent ->
                    mutableHistoryUnavailable.value = false
                    mutableRecentMedia.value = recent.map(RecentMedia::toUiItem)
                }
        }
    }

    fun onDocumentSelected(
        selection: OpenMediaDocumentResult,
        hasObservedCompletion: (String) -> Boolean = { false },
    ) {
        val requestId = beginPlaybackOperation()
        mediaOperationJob = viewModelScope.launch {
            try {
                val opened = dependencies.mediaDocumentRegistrar.open(
                    uri = selection.uri,
                    persistableReadOffered = selection.offersPersistableRead,
                )
                var notice = opened.persistence.toNotice()
                val checkpoint = if (opened.persistence == MediaDocumentPersistence.PERSISTED) {
                    try {
                        repository.findById(opened.mediaId)?.checkpoint
                    } catch (failure: CancellationException) {
                        throw failure
                    } catch (_: Exception) {
                        notice = notice ?: MediaSelectionNotice.HISTORY_UNAVAILABLE
                        null
                    }
                } else {
                    null
                }
                publishPlayback(
                    requestId = requestId,
                    opened = opened,
                    checkpoint = checkpoint,
                    notice = notice,
                    startFromBeginning = hasObservedCompletion(opened.mediaId.value),
                )
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                publishFailure(requestId, MediaSelectionNotice.OPEN_FAILED)
            }
        }
    }

    fun onRecentSelected(
        mediaId: String,
        startFromBeginning: Boolean = false,
    ) {
        val requestId = beginPlaybackOperation()
        mediaOperationJob = viewModelScope.launch {
            try {
                val stored = repository.findById(MediaId(mediaId))
                if (stored == null) {
                    publishFailure(requestId, MediaSelectionNotice.RECENT_ACCESS_LOST)
                    return@launch
                }
                val opened = dependencies.mediaDocumentRegistrar.open(
                    uri = Uri.parse(stored.record.sourceUri.value),
                    persistableReadOffered = false,
                )
                when (opened.persistence) {
                    MediaDocumentPersistence.SESSION_ONLY_PERMISSION_UNAVAILABLE ->
                        publishFailure(requestId, MediaSelectionNotice.RECENT_ACCESS_LOST)

                    MediaDocumentPersistence.SESSION_ONLY_HISTORY_WRITE_FAILED ->
                        publishPlayback(
                            requestId = requestId,
                            opened = opened,
                            checkpoint = stored.checkpoint,
                            notice = MediaSelectionNotice.SESSION_ONLY_HISTORY,
                            startFromBeginning = startFromBeginning,
                        )

                    MediaDocumentPersistence.PERSISTED -> {
                        var notice: MediaSelectionNotice? = null
                        val checkpoint = try {
                            repository.findById(opened.mediaId)?.checkpoint
                        } catch (failure: CancellationException) {
                            throw failure
                        } catch (_: Exception) {
                            notice = MediaSelectionNotice.HISTORY_UNAVAILABLE
                            stored.checkpoint
                        }
                        publishPlayback(
                            requestId = requestId,
                            opened = opened,
                            checkpoint = checkpoint,
                            notice = notice,
                            startFromBeginning = startFromBeginning,
                        )
                    }
                }
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                publishFailure(requestId, MediaSelectionNotice.OPEN_FAILED)
            }
        }
    }

    fun onLibraryPlaylist(items: List<LibraryMedia>, startIndex: Int) {
        val requestId = beginPlaybackOperation()
        if (items.isEmpty() || startIndex !in items.indices || items.size > 500) {
            publishFailure(requestId, MediaSelectionNotice.QUEUE_TOO_LARGE)
            return
        }
        val selected = items.toList()
        mediaOperationJob = viewModelScope.launch {
            try {
                val documents = selected.map { item ->
                    kotlinx.coroutines.currentCoroutineContext().ensureActive()
                    dependencies.mediaDocumentRegistrar.open(Uri.parse(item.sourceUri.value),
                        persistableReadOffered = false, visibleInHistory = false)
                }
                val opened = documents[startIndex]
                val checkpoint = repository.findById(opened.mediaId)?.checkpoint
                val resume = dependencies.playerPreferences.preferences.first().resumePlayback
                if (requestId != latestRequestId) return@launch
                mutablePendingPlayback.value = PendingMediaPlayback(requestId, UUID.randomUUID().toString(), opened,
                    checkpoint.resumePositionMs(!resume), documents, startIndex)
                mutableNotice.value = documents.firstNotNullOfOrNull { it.persistence.toNotice() }
            } catch (failure: CancellationException) { throw failure
            } catch (_: Exception) { publishFailure(requestId, MediaSelectionNotice.OPEN_FAILED) }
        }
    }

    fun forgetRecent(mediaId: String) {
        val requestId = beginMediaOperation()
        mediaOperationJob = viewModelScope.launch {
            val notice = try {
                when (dependencies.mediaDocumentRegistrar.forget(MediaId(mediaId))) {
                    MediaDocumentForgetResult.NOT_FOUND ->
                        MediaSelectionNotice.HISTORY_FORGET_FAILED

                    MediaDocumentForgetResult.REMOVED -> {
                        if (dependencies.documentTreeLibrary.releaseUnusedTreeGrants() == 0)
                            MediaSelectionNotice.HISTORY_FORGOTTEN
                        else MediaSelectionNotice.HISTORY_FORGOTTEN_WITH_ORPHANED_GRANT
                    }

                    MediaDocumentForgetResult.REMOVED_WITH_ORPHANED_GRANT ->
                        MediaSelectionNotice.HISTORY_FORGOTTEN_WITH_ORPHANED_GRANT
                }
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                MediaSelectionNotice.HISTORY_FORGET_FAILED
            }
            if (requestId == latestRequestId) {
                mutableNotice.value = notice
            }
        }
    }

    fun onPlaybackConsumed(requestId: Long) {
        if (mutablePendingPlayback.value?.requestId == requestId) {
            mutablePendingPlayback.value = null
            playbackRequestId = null
        }
    }

    fun onPlaybackFailed(requestId: Long) {
        if (mutablePendingPlayback.value?.requestId == requestId) {
            mutablePendingPlayback.value = null
            mutableNotice.value = MediaSelectionNotice.OPEN_FAILED
            playbackRequestId = null
        }
    }

    fun isPlaybackPending(requestId: Long): Boolean =
        playbackRequestId == requestId && mutablePendingPlayback.value?.requestId == requestId

    /** Gives an explicit transport action priority over an in-flight auto-play request. */
    fun cancelPendingPlayback() {
        val requestId = playbackRequestId ?: return
        if (requestId == latestRequestId) latestRequestId = PlaybackRequestSequencer.invalidate()
        mediaOperationJob?.cancel()
        mediaOperationJob = null
        playbackRequestId = null
        if (mutablePendingPlayback.value?.requestId == requestId) {
            mutablePendingPlayback.value = null
        }
    }

    private fun beginPlaybackOperation(): Long = beginMediaOperation().also { requestId ->
        playbackRequestId = requestId
    }

    private fun beginMediaOperation(): Long {
        val requestId = PlaybackRequestSequencer.next()
        latestRequestId = requestId
        mediaOperationJob?.cancel()
        playbackRequestId = null
        mutablePendingPlayback.value = null
        mutableNotice.value = null
        return requestId
    }

    private suspend fun publishPlayback(
        requestId: Long,
        opened: OpenedMediaDocument,
        checkpoint: PlaybackCheckpoint?,
        notice: MediaSelectionNotice?,
        startFromBeginning: Boolean = false,
    ) {
        val resumeEnabled = dependencies.playerPreferences.preferences.first().resumePlayback
        if (requestId != latestRequestId) return
        mutablePendingPlayback.value = PendingMediaPlayback(
            requestId = requestId,
            dispatchToken = UUID.randomUUID().toString(),
            document = opened,
            startPositionMs = checkpoint.resumePositionMs(startFromBeginning || !resumeEnabled),
        )
        mutableNotice.value = notice
    }

    private fun publishFailure(requestId: Long, notice: MediaSelectionNotice) {
        if (requestId == latestRequestId) {
            mutablePendingPlayback.value = null
            mutableNotice.value = notice
            playbackRequestId = null
        }
    }
}

private fun MediaDocumentPersistence.toNotice(): MediaSelectionNotice? = when (this) {
    MediaDocumentPersistence.PERSISTED -> null
    MediaDocumentPersistence.SESSION_ONLY_PERMISSION_UNAVAILABLE ->
        MediaSelectionNotice.SESSION_ONLY_PERMISSION

    MediaDocumentPersistence.SESSION_ONLY_HISTORY_WRITE_FAILED ->
        MediaSelectionNotice.SESSION_ONLY_HISTORY
}

internal fun PlaybackCheckpoint?.resumePositionMs(startFromBeginning: Boolean = false): Long {
    if (startFromBeginning) return 0L
    val checkpoint = this?.takeUnless(PlaybackCheckpoint::completed) ?: return 0L
    val position = checkpoint.position.value
    return checkpoint.duration?.value?.let { duration -> position.coerceIn(0L, duration) }
        ?: position.coerceAtLeast(0L)
}

private fun RecentMedia.toUiItem(): RecentMediaUiItem {
    val title = record.metadata.title.cleanText()
        ?: Uri.parse(record.sourceUri.value).lastPathSegment.cleanText()
        ?: record.id.value
    val supportingText = listOfNotNull(
        record.metadata.artist.cleanText(),
        record.metadata.album.cleanText(),
    ).distinct().joinToString(METADATA_SEPARATOR).ifBlank { null }
    return RecentMediaUiItem(
        mediaId = record.id.value,
        title = title,
        supportingText = supportingText,
        positionMs = checkpoint?.position?.value,
        durationMs = checkpoint?.duration?.value,
        completed = checkpoint?.completed == true,
    )
}

private fun String?.cleanText(): String? = this?.takeUnless(String::isBlank)

private const val RECENT_MEDIA_LIMIT = 6
private const val METADATA_SEPARATOR = " · "
