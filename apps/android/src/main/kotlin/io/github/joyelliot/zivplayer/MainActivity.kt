// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.os.Bundle
import android.os.Build
import android.app.PendingIntent
import android.app.PictureInPictureParams
import android.app.RemoteAction
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.drawable.Icon
import android.util.Rational
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import io.github.joyelliot.zivplayer.data.media.toDiagnosticJson
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.launch
import kotlin.math.roundToInt
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

class MainActivity : ComponentActivity() {
    private val playbackController by viewModels<PlaybackControllerViewModel>()
    private val mediaSelection by viewModels<MediaSelectionViewModel>()
    private val library by viewModels<LibraryViewModel>()
    private val settings by viewModels<SettingsViewModel>()
    private var fullscreen by mutableStateOf(false)
    private var inPictureInPicture by mutableStateOf(false)
    private var playerVisible by mutableStateOf(true)
    private var pipEntryPending = false
    private var started = false
    private var diagnosticsPageVisible = false
    private var pendingDiagnosticJson: String? = null
    private val exportDiagnostics = registerForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        val json = pendingDiagnosticJson
        pendingDiagnosticJson = null
        if (uri != null && json != null) lifecycleScope.launch {
            val result = try {
                (application as ZivPlayerApplication).diagnosticsExporter.write(uri, json)
                R.string.diagnostics_exported
            } catch (_: TimeoutCancellationException) { R.string.diagnostics_export_failed
            } catch (failure: CancellationException) { throw failure
            } catch (_: Exception) { R.string.diagnostics_export_failed }
            Toast.makeText(this@MainActivity, result, Toast.LENGTH_LONG).show()
        }
    }
    private val pipReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == PIP_TOGGLE_ACTION) playbackController.togglePlayPause()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        fullscreen = savedInstanceState?.getBoolean("fullscreen") ?: false
        inPictureInPicture = isInPictureInPictureMode
        pendingDiagnosticJson = savedInstanceState?.getString("pending-diagnostics-export")
        if (!inPictureInPicture) changeFullscreen(fullscreen)
        ContextCompat.registerReceiver(this, pipReceiver, IntentFilter(PIP_TOGGLE_ACTION), ContextCompat.RECEIVER_NOT_EXPORTED)
        enableEdgeToEdge()
        setContent {
            val playing = playbackController.playerState.value.playWhenReady
            val hasVideo = playbackController.playerState.value.hasVideo
            val canRenderVideo = playbackController.playerState.value.canRenderVideo
            val aspectRatio = playbackController.playerState.value.videoAspectRatio
            val preferences = settings.preferences.value
            LaunchedEffect(playing, preferences.keepScreenOn) {
                if (playing && preferences.keepScreenOn) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
            LaunchedEffect(fullscreen, inPictureInPicture) {
                val bars = WindowCompat.getInsetsController(window, window.decorView)
                bars.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                if (fullscreen && !inPictureInPicture) bars.hide(WindowInsetsCompat.Type.systemBars())
                else bars.show(WindowInsetsCompat.Type.systemBars())
            }
            LaunchedEffect(playing, hasVideo, canRenderVideo, aspectRatio, preferences.autoPictureInPicture, settings.loaded.value, playerVisible) { updatePipParameters() }
            ZivPlayerApp(
                library = library,
                settings = settings,
                fullscreen = fullscreen,
                pictureInPicture = inPictureInPicture,
                onFullscreenChange = ::changeFullscreen,
                onEnterPictureInPicture = { enterPip(showFailure = true) },
                onPlayerVisibilityChange = { playerVisible = it },
                diagnostics = playbackController.diagnostics.value,
                diagnosticsMessage = playbackController.diagnosticsMessage.value,
                configurationMessage = playbackController.configurationMessage.value,
                onDiagnosticsVisibilityChange = {
                    diagnosticsPageVisible = it
                    playbackController.setDiagnosticsVisible(started && it)
                },
                onExportDiagnostics = {
                    val sample = playbackController.diagnostics.value
                    val sampledAt = playbackController.diagnosticsSampledAtEpochMs
                    if (sample != null && sampledAt != null) {
                        pendingDiagnosticJson = sample.toDiagnosticJson(sampledAt, System.currentTimeMillis())
                        exportDiagnostics.launch("zivplayer-diagnostics.json")
                    }
                },
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
                onSelectTrack = playbackController::selectTrack,
                onBeginSubtitleSelection = playbackController::beginSubtitleSelection,
                onSubtitleSelected = playbackController::onSubtitleSelected,
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

    private fun changeFullscreen(value: Boolean) {
        fullscreen = value
        requestedOrientation = if (value) ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
    }

    private fun pipSupported() = packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)

    private fun pipParameters(): PictureInPictureParams {
        val state = playbackController.playerState.value
        val intent = Intent(PIP_TOGGLE_ACTION).setPackage(packageName)
        val action = RemoteAction(Icon.createWithResource(this, if (state.playWhenReady) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play),
            getString(R.string.app_play_pause), getString(R.string.app_play_pause),
            PendingIntent.getBroadcast(this, 1, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        val ratio = state.videoAspectRatio.coerceIn(0.42f, 2.38f)
        return PictureInPictureParams.Builder().setAspectRatio(Rational((ratio * 10_000).roundToInt(), 10_000)).setActions(listOf(action)).apply {
            if (Build.VERSION.SDK_INT >= 31) {
                setAutoEnterEnabled(settings.loaded.value && settings.preferences.value.autoPictureInPicture && playerVisible && state.hasVideo && state.canRenderVideo && state.playWhenReady)
                setSeamlessResizeEnabled(true)
            }
        }.build()
    }

    private fun updatePipParameters() {
        if (pipSupported()) runCatching { setPictureInPictureParams(pipParameters()) }
    }

    private fun enterPip(showFailure: Boolean) {
        if (inPictureInPicture) return
        val accepted = pipSupported() && playbackController.playerState.value.hasVideo && playbackController.playerState.value.canRenderVideo &&
            runCatching { enterPictureInPictureMode(pipParameters()) }.getOrDefault(false)
        pipEntryPending = accepted
        if (!accepted && showFailure) Toast.makeText(this, R.string.app_pip_unavailable, Toast.LENGTH_SHORT).show()
    }

    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (Build.VERSION.SDK_INT < 31 && settings.loaded.value && settings.preferences.value.autoPictureInPicture &&
            playerVisible && playbackController.playerState.value.playWhenReady) enterPip(showFailure = false)
    }

    override fun onPictureInPictureModeChanged(isInPictureInPictureMode: Boolean, newConfig: Configuration) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        inPictureInPicture = isInPictureInPictureMode
        pipEntryPending = false
    }

    override fun onStop() {
        super.onStop()
        started = false
        playbackController.setDiagnosticsVisible(false)
        if (!isChangingConfigurations && !isInPictureInPictureMode && !pipEntryPending &&
            (!settings.loaded.value || !settings.preferences.value.backgroundPlayback)) {
            mediaSelection.cancelPendingPlayback()
            playbackController.pause()
        }
    }

    override fun onStart() {
        super.onStart()
        started = true
        pipEntryPending = false
        playbackController.setDiagnosticsVisible(diagnosticsPageVisible)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("fullscreen", fullscreen)
        outState.putString("pending-diagnostics-export", pendingDiagnosticJson)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        unregisterReceiver(pipReceiver)
        super.onDestroy()
    }

    private companion object { const val PIP_TOGGLE_ACTION = "io.github.joyelliot.zivplayer.PIP_TOGGLE" }
}
