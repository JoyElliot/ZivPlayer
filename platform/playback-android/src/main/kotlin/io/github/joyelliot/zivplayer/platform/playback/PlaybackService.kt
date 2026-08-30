// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(
    markerClass = [
        androidx.media3.common.util.ExperimentalApi::class,
        androidx.media3.common.util.UnstableApi::class,
    ],
)

package io.github.joyelliot.zivplayer.platform.playback

import android.app.PendingIntent
import android.os.Process
import android.util.Log
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.google.common.util.concurrent.MoreExecutors
import io.github.joyelliot.zivplayer.core.player.runtime.DefaultPlayerSession
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvBackend

/** Sole owner of the native player, core session, Media3 player, and MediaSession. */
class PlaybackService : MediaSessionService() {
    private var mediaSession: MediaSession? = null
    private var player: MpvSessionPlayer? = null

    override fun onCreate() {
        super.onCreate()
        var createdPlayer: MpvSessionPlayer? = null
        var createdSession: MediaSession? = null
        try {
            val historyProvider = application as? PlaybackHistoryProvider
                ?: error("The application must provide playback history dependencies.")
            val progressRecorder = PlaybackProgressRecorder(
                repository = historyProvider.recentMediaRepository,
                failureReporter = { failure ->
                    Log.w(LOG_TAG, "Playback progress could not be persisted.", failure)
                },
            )
            createdPlayer = MpvSessionPlayer(
                applicationLooper = mainLooper,
                context = this,
                progressRecorder = progressRecorder,
            ) {
                val backend = LibmpvBackend(this)
                PlaybackEngine(
                    session = DefaultPlayerSession(backend),
                    surfacePort = backend,
                )
            }
            val sessionBuilder = MediaSession.Builder(this, createdPlayer)
                .setCallback(CONTROLLER_GATE)
                .setExperimentalSetUseLegacySurfaceHandling(true)
            packageManager.getLaunchIntentForPackage(packageName)?.let { launchIntent ->
                sessionBuilder.setSessionActivity(
                    PendingIntent.getActivity(
                        this,
                        SESSION_ACTIVITY_REQUEST_CODE,
                        launchIntent,
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                    ),
                )
            }
            createdSession = sessionBuilder.build()
            player = createdPlayer
            mediaSession = createdSession
        } catch (failure: Throwable) {
            runCatching { createdSession?.release() }
            beginPlayerShutdown(createdPlayer).exceptionOrNull()?.let(failure::addSuppressed)
            throw failure
        }
    }

    override fun onGetSession(
        controllerInfo: MediaSession.ControllerInfo,
    ): MediaSession? = mediaSession

    override fun onDestroy() {
        val ownedPlayer = player
        player = null
        val ownedSession = mediaSession
        mediaSession = null
        var cleanupFailure: Throwable? = beginPlayerShutdown(ownedPlayer).exceptionOrNull()
        runCatching { ownedSession?.release() }
            .exceptionOrNull()
            ?.let { failure ->
                cleanupFailure?.addSuppressed(failure) ?: run { cleanupFailure = failure }
            }
        try {
            super.onDestroy()
        } catch (failure: Throwable) {
            cleanupFailure?.addSuppressed(failure) ?: run { cleanupFailure = failure }
        }
        cleanupFailure?.let { failure ->
            Log.e(LOG_TAG, "Playback service cleanup did not complete cleanly.", failure)
        }
    }

    private fun beginPlayerShutdown(ownedPlayer: MpvSessionPlayer?): Result<Unit> = runCatching {
        if (ownedPlayer != null) {
            val shutdownFuture = ownedPlayer.shutdownAsync()
            shutdownFuture.addListener(
                {
                    runCatching { shutdownFuture.get() }
                        .onFailure { failure ->
                            Log.e(LOG_TAG, "Asynchronous player shutdown failed.", failure)
                        }
                },
                MoreExecutors.directExecutor(),
            )
            try {
                ownedPlayer.release()
            } catch (failure: Throwable) {
                if (!shutdownFuture.isDone) {
                    Log.w(LOG_TAG, "Player release failed while shutdown continues.", failure)
                }
                throw failure
            }
        }
    }

    private companion object {
        val CONTROLLER_GATE = object : MediaSession.Callback {
            override fun onConnect(
                session: MediaSession,
                controller: MediaSession.ControllerInfo,
            ): MediaSession.ConnectionResult {
                val allowed = controller.uid == Process.myUid() ||
                    controller.isTrusted ||
                    session.isMediaNotificationController(controller) ||
                    session.isAutomotiveController(controller) ||
                    session.isAutoCompanionController(controller)
                return if (allowed) {
                    super.onConnect(session, controller)
                } else {
                    MediaSession.ConnectionResult.reject()
                }
            }
        }
        const val LOG_TAG = "ZivPlaybackService"
        const val SESSION_ACTIVITY_REQUEST_CODE = 0
    }
}
