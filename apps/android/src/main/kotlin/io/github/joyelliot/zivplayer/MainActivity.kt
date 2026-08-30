// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels

class MainActivity : ComponentActivity() {
    private val playbackController by viewModels<PlaybackControllerViewModel>()
    private val mediaSelection by viewModels<MediaSelectionViewModel>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ZivPlayerApp(
                controller = playbackController.controller.value,
                pendingPlayback = mediaSelection.pendingPlayback.value,
                persistenceNotice = mediaSelection.persistenceNotice.value,
                selectionFailed = mediaSelection.selectionFailed.value,
                onDocumentSelected = mediaSelection::onDocumentSelected,
                onPlaybackConsumed = mediaSelection::onPlaybackConsumed,
                onPlaybackFailed = mediaSelection::onPlaybackFailed,
            )
        }
    }
}
