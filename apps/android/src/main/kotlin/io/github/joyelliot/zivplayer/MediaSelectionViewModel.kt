// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.joyelliot.zivplayer.data.media.MediaDocumentPersistence
import io.github.joyelliot.zivplayer.data.media.OpenedMediaDocument
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

data class PendingMediaPlayback(
    val requestId: Long,
    val document: OpenedMediaDocument,
)

/** Retains document registration and pending playback across Activity recreation. */
class MediaSelectionViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val dependencies = application as ZivPlayerApplication
    private val mutablePendingPlayback = mutableStateOf<PendingMediaPlayback?>(null)
    val pendingPlayback: State<PendingMediaPlayback?> = mutablePendingPlayback

    private val mutablePersistenceNotice = mutableStateOf<MediaDocumentPersistence?>(null)
    val persistenceNotice: State<MediaDocumentPersistence?> = mutablePersistenceNotice

    private val mutableSelectionFailed = mutableStateOf(false)
    val selectionFailed: State<Boolean> = mutableSelectionFailed

    private var latestRequestId = 0L
    private var registrationJob: Job? = null

    fun onDocumentSelected(selection: OpenMediaDocumentResult) {
        val requestId = ++latestRequestId
        registrationJob?.cancel()
        mutablePendingPlayback.value = null
        mutablePersistenceNotice.value = null
        mutableSelectionFailed.value = false
        registrationJob = viewModelScope.launch {
            try {
                val opened = dependencies.mediaDocumentRegistrar.open(
                    uri = selection.uri,
                    persistableReadOffered = selection.offersPersistableRead,
                )
                if (requestId == latestRequestId) {
                    mutablePendingPlayback.value = PendingMediaPlayback(requestId, opened)
                    mutablePersistenceNotice.value = opened.persistence
                        .takeUnless { it == MediaDocumentPersistence.PERSISTED }
                }
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                if (requestId == latestRequestId) {
                    mutableSelectionFailed.value = true
                }
            }
        }
    }

    fun onPlaybackConsumed(requestId: Long) {
        if (mutablePendingPlayback.value?.requestId == requestId) {
            mutablePendingPlayback.value = null
        }
    }

    fun onPlaybackFailed(requestId: Long) {
        if (mutablePendingPlayback.value?.requestId == requestId) {
            mutablePendingPlayback.value = null
            mutableSelectionFailed.value = true
        }
    }
}
