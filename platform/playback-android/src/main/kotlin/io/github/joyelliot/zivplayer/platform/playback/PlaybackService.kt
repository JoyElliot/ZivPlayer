// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(
    markerClass = [
        androidx.media3.common.util.ExperimentalApi::class,
        androidx.media3.common.util.UnstableApi::class,
    ],
)

package io.github.joyelliot.zivplayer.platform.playback

import android.app.PendingIntent
import android.os.Bundle
import android.os.Process
import android.util.Log
import androidx.core.net.toUri
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionError
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.SettableFuture
import com.google.common.util.concurrent.MoreExecutors
import io.github.joyelliot.zivplayer.core.player.runtime.DefaultPlayerSession
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvBackend

/** Sole owner of the native player, core session, Media3 player, and MediaSession. */
class PlaybackService : MediaSessionService() {
    private var mediaSession: MediaSession? = null
    private var player: MpvSessionPlayer? = null
    private val foregroundGate = PlaybackForegroundGate(this)

    override fun onCreate() {
        super.onCreate()
        setMediaNotificationProvider(DefaultMediaNotificationProvider(this).apply {
            setSmallIcon(android.R.drawable.ic_media_play)
        })
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
                ensurePlaybackForeground = { foregroundGate.begin(checkNotNull(mediaSession)) },
                cancelPendingForeground = foregroundGate::cancelPendingPromotion,
                onPlaybackPublished = foregroundGate::onPlaybackPublished,
            ) {
                val backend = LibmpvBackend(this)
                PlaybackEngine(
                    session = DefaultPlayerSession(backend),
                    surfacePort = backend,
                )
            }
            val sessionBuilder = MediaSession.Builder(this, createdPlayer)
                .setCallback(controllerGate)
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

    private val controllerGate = object : MediaSession.Callback {
        override fun onConnect(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): MediaSession.ConnectionResult {
            val allowed = controller.uid == Process.myUid() ||
                controller.isTrusted ||
                session.isMediaNotificationController(controller) ||
                session.isAutomotiveController(controller) ||
                session.isAutoCompanionController(controller)
            if (!allowed) return MediaSession.ConnectionResult.reject()
            val sessionCommands = MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
            if (controller.uid == Process.myUid()) {
                sessionCommands
                    .add(SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY))
                    .add(SessionCommand(VideoSurfaceRequestContract.ACTION_RESIZE, Bundle.EMPTY))
            }
            // Media3 1.11's deprecated default callback returns an empty command set.
            // This is the connection's static ceiling; the player's current capabilities
            // still constrain it, including commands that become available after prepare.
            return MediaSession.ConnectionResult.AcceptedResultBuilder(session, controller)
                .setAvailableSessionCommands(sessionCommands.build())
                .setAvailablePlayerCommands(MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS)
                .build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            if (customCommand.customAction != SubtitleRequestContract.ACTION_ADD &&
                customCommand.customAction != VideoSurfaceRequestContract.ACTION_RESIZE) {
                return super.onCustomCommand(session, controller, customCommand, args)
            }
            if (controller.uid != Process.myUid()) {
                return Futures.immediateFuture(SessionResult(SessionError.ERROR_PERMISSION_DENIED))
            }
            if (customCommand.customAction == VideoSurfaceRequestContract.ACTION_RESIZE) {
                val ownedPlayer = player ?: return Futures.immediateFuture(SessionResult(SessionError.ERROR_INVALID_STATE))
                return commandReply(ownedPlayer.resizeVideoSurface(
                    args.getLong(VideoSurfaceRequestContract.TOKEN),
                    args.getInt(VideoSurfaceRequestContract.WIDTH),
                    args.getInt(VideoSurfaceRequestContract.HEIGHT),
                ))
            }
            val uri = args.getString(SubtitleRequestContract.URI)
            val mediaId = args.getString(SubtitleRequestContract.MEDIA_ID)
            val sequence = args.getLong(SubtitleRequestContract.REQUEST_SEQUENCE, -1)
            val ownedPlayer = player
            if (uri.isNullOrBlank() || mediaId.isNullOrBlank() || sequence <= 0 || ownedPlayer == null) {
                return Futures.immediateFuture(SessionResult(SessionError.ERROR_BAD_VALUE))
            }
            val operation = ownedPlayer.addSubtitle(uri.toUri(), mediaId, sequence,
                args.getString(SubtitleRequestContract.TITLE))
            return commandReply(operation)
        }
    }

    private fun commandReply(operation: ListenableFuture<*>): ListenableFuture<SessionResult> {
        val reply = SettableFuture.create<SessionResult>()
        operation.addListener({
            val failure = runCatching { operation.get() }.exceptionOrNull()
            reply.set(if (failure == null) SessionResult(SessionResult.RESULT_SUCCESS) else {
                SessionResult(SessionError.ERROR_IO, Bundle().apply {
                    putString(SubtitleRequestContract.ERROR_MESSAGE,
                        failure.cause?.message ?: failure.message ?: "The playback command failed.")
                })
            })
        }, MoreExecutors.directExecutor())
        return reply
    }

    private companion object {
        const val LOG_TAG = "ZivPlaybackService"
        const val SESSION_ACTIVITY_REQUEST_CODE = 0
    }
}
