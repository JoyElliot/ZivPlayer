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
                playerState = playbackController.playerState.value,
                pendingPlayback = mediaSelection.pendingPlayback.value,
                recentMedia = mediaSelection.recentMedia.value,
                selectionNotice = mediaSelection.notice.value,
                historyUnavailable = mediaSelection.historyUnavailable.value,
                onDocumentSelected = { selection ->
                    mediaSelection.onDocumentSelected(
                        selection = selection,
                        hasObservedCompletion = playbackController::hasObservedCompletion,
                    )
                },
                onPlaybackConsumed = mediaSelection::onPlaybackConsumed,
                onPlaybackFailed = mediaSelection::onPlaybackFailed,
                isPlaybackPending = mediaSelection::isPlaybackPending,
                onPlayPause = {
                    mediaSelection.cancelPendingPlayback()
                    playbackController.togglePlayPause()
                },
                onStop = {
                    mediaSelection.cancelPendingPlayback()
                    playbackController.stop()
                },
                onSeekTo = playbackController::seekTo,
                onPlaybackSpeedChange = playbackController::setPlaybackSpeed,
                onVolumeChange = playbackController::setVolume,
                onRepeatModeChange = playbackController::setRepeatMode,
                onOpenRecent = { mediaId ->
                    mediaSelection.onRecentSelected(
                        mediaId = mediaId,
                        startFromBeginning = playbackController.hasObservedCompletion(mediaId),
                    )
                },
                onForgetRecent = mediaSelection::forgetRecent,
            )
        }
    }
}
