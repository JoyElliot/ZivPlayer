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
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlin.math.roundToInt
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableFloatStateOf
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
    private var rotationBasis by mutableStateOf<PlayerRotationBasis?>(null)
    private var measuredWindowRotation: Int? = null
    private var rotationAnimationSettled = true
    private var rotationUnavailable by mutableStateOf(false)
    private var orientationCheck: Job? = null
    private var manualPipRequested = false
    private var immediatePipAlignment by mutableStateOf(false)
    private var videoAlignedWithWindow = true
    private var inPictureInPicture by mutableStateOf(false)
    private var playerVisible by mutableStateOf(false)
    private var playbackBrightness by mutableStateOf<Float?>(null)
    private var systemBrightness by mutableFloatStateOf(0.5f)
    private var originalWindowBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
    private var pipEntryPending = false
    private var pipTransitioning by mutableStateOf(false)
    private var videoBounds: android.graphics.Rect? = null
    private var publishedPipParameters: PictureInPictureParams? = null
    private val hasPipSupport by lazy { packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE) }
    private val pipToggleIntent by lazy {
        PendingIntent.getBroadcast(this, 1, Intent(PIP_TOGGLE_ACTION).setPackage(packageName),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }
    private val pipPlayAction by lazy { pipAction(android.R.drawable.ic_media_play) }
    private val pipPauseAction by lazy { pipAction(android.R.drawable.ic_media_pause) }
    private var stoppedPlaybackCheck: Job? = null
    private var started by mutableStateOf(false)
    private var windowFocused by mutableStateOf(false)
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
        volumeControlStream = android.media.AudioManager.STREAM_MUSIC
        originalWindowBrightness = window.attributes.screenBrightness
        systemBrightness = (android.provider.Settings.System.getInt(contentResolver,
            android.provider.Settings.System.SCREEN_BRIGHTNESS, 128) / 255f).coerceIn(0f, 1f)
        playbackBrightness = savedInstanceState?.takeIf { it.containsKey("playback-brightness") }?.getFloat("playback-brightness")
        fullscreen = savedInstanceState?.getBoolean("fullscreen") ?: false
        inPictureInPicture = isInPictureInPictureMode
        rotationBasis = savedInstanceState?.takeIf { it.containsKey("rotation-basis") }?.let {
            PlayerRotationBasis(it.getInt("rotation-basis"), it.getBoolean("rotation-basis-landscape"))
        }
        window.attributes = window.attributes.apply {
            // The player already animates the turn. SEAMLESS may fall back to a second WM
            // rotation (for example when the navigation bar changes sides on Xiaomi).
            rotationAnimation = WindowManager.LayoutParams.ROTATION_ANIMATION_JUMPCUT
        }
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
            val hideSystemBars = (fullscreen || playerVisible || rotationBasis != null) && !inPictureInPicture
            LaunchedEffect(hideSystemBars) {
                val bars = WindowCompat.getInsetsController(window, window.decorView)
                bars.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                if (hideSystemBars) {
                    // The rotation-animation hint requires an opaque fullscreen window.
                    window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
                    bars.hide(WindowInsetsCompat.Type.systemBars())
                } else {
                    window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
                    bars.show(WindowInsetsCompat.Type.systemBars())
                }
            }
            val desiredBrightness = if (playerVisible && !inPictureInPicture)
                playbackBrightness?.coerceIn(0.01f, 1f) ?: originalWindowBrightness else originalWindowBrightness
            LaunchedEffect(desiredBrightness) {
                window.attributes = window.attributes.apply {
                    screenBrightness = desiredBrightness
                }
            }
            LaunchedEffect(playing, hasVideo, canRenderVideo, aspectRatio, preferences.autoPictureInPicture, settings.loaded.value, playerVisible) { updatePipParameters() }
            ZivPlayerApp(
                library = library,
                settings = settings,
                interactionEnabled = started && windowFocused,
                onBeginTemporarySpeed = playbackController::beginTemporarySpeed,
                onTemporarySpeedChange = playbackController::updateTemporarySpeed,
                onEndTemporarySpeed = playbackController::endTemporarySpeed,
                fullscreen = fullscreen,
                rotationBasis = rotationBasis,
                rotationUnavailable = rotationUnavailable,
                rotationActive = started,
                onRotationAnimationFinished = ::onRotationAnimationFinished,
                onWindowRotation = { measuredWindowRotation = it; releaseRotationIfIdle() },
                onRotationAlignmentChange = {
                    if (videoAlignedWithWindow != it) {
                        videoAlignedWithWindow = it
                        updatePipParameters()
                    }
                },
                pictureInPicture = inPictureInPicture,
                pictureInPictureTransition = pipTransitioning,
                pictureInPictureImmediateTransition = immediatePipAlignment,
                onVideoBounds = { bounds ->
                    val visibleBounds = android.graphics.Rect(bounds)
                    val visible = visibleBounds.intersect(0, 0, window.decorView.width, window.decorView.height)
                    if (!inPictureInPicture && visible && videoBounds != visibleBounds) {
                        videoBounds = visibleBounds
                        // Keep the latest local bounds during motion; publish once the expanded
                        // video is aligned. Explicit PiP entry reads videoBounds directly.
                        if (videoAlignedWithWindow && !pipTransitioning) updatePipParameters()
                    }
                },
                onFullscreenChange = ::changeFullscreen,
                onEnterPictureInPicture = { enterPip(showFailure = true) },
                onCancelPictureInPicture = ::cancelManualPipEntry,
                onPlayerVisibilityChange = {
                    playerVisible = it
                    if (it && rotationBasis == null && !inPictureInPicture) {
                        rotationBasis = PlayerRotationBasis(display?.rotation ?: 0,
                            resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE)
                        measuredWindowRotation = rotationBasis?.rotation
                        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LOCKED
                    }
                    releaseRotationIfIdle()
                },
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
                    playbackController.cancelTemporarySpeed()
                    mediaSelection.onDocumentSelected(
                        selection = selection,
                        hasObservedCompletion = playbackController::hasObservedCompletion,
                    )
                },
                onPlaybackConsumed = mediaSelection::onPlaybackConsumed,
                onLibraryPlaylist = { items, index ->
                    playbackController.cancelTemporarySpeed()
                    mediaSelection.onLibraryPlaylist(items, index)
                },
                onSelectQueueItem = { index -> mediaSelection.cancelPendingPlayback(); playbackController.selectQueueItem(index) },
                onPrevious = { mediaSelection.cancelPendingPlayback(); playbackController.previous() },
                onNext = { mediaSelection.cancelPendingPlayback(); playbackController.next() },
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
                onSeekTo = { position -> mediaSelection.cancelPendingPlayback(); playbackController.seekTo(position) },
                onSeekPreviewTo = playbackController::previewSeekTo,
                onSeekPreviewFinished = playbackController::finishSeekPreview,
                onPlaybackSpeedChange = playbackController::setPlaybackSpeed,
                onVolumeChange = playbackController::setVolume,
                brightness = playbackBrightness ?: systemBrightness,
                onBrightnessChange = { playbackBrightness = it.coerceIn(0f, 1f) },
                onRepeatModeChange = playbackController::setRepeatMode,
                onSelectTrack = playbackController::selectTrack,
                onBeginSubtitleSelection = playbackController::beginSubtitleSelection,
                onSubtitleSelected = playbackController::onSubtitleSelected,
                onOpenRecent = { mediaId ->
                    playbackController.cancelTemporarySpeed()
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
        playbackController.cancelTemporarySpeed()
        cancelManualPipEntry()
        if (fullscreen != value) {
            videoAlignedWithWindow = false
            updatePipParameters()
            rotationAnimationSettled = false
            rotationUnavailable = false
            orientationCheck?.cancel()
        }
        fullscreen = value
    }

    private fun cancelManualPipEntry() {
        if (manualPipRequested) {
            manualPipRequested = false
            immediatePipAlignment = false
            pipEntryPending = false
            pipTransitioning = false
        }
    }

    private fun onRotationAnimationFinished(turned: Boolean, pipTransition: Boolean) {
        // Compose cancels superseded animation jobs; also reject callbacks after lifecycle/PiP changes.
        if (inPictureInPicture || turned != fullscreen || pipTransition != pipTransitioning) return
        if (pipTransition) {
            if (manualPipRequested && started) completePipEntry()
            return
        }
        rotationAnimationSettled = true
        val basis = rotationBasis ?: return
        if (!started) return
        val target = basis.targetRotation(turned)
        val naturalLandscape = basis.landscape != (basis.rotation % 2 != 0)
        val reverseDefault = runCatching {
            val id = resources.getIdentifier("config_reverseDefaultRotation", "bool", "android")
            id != 0 && resources.getBoolean(id)
        }.getOrDefault(false)
        // Fixed orientations avoid sensor changes choosing the opposite 90-degree endpoint.
        requestedOrientation = fixedOrientationForRotation(target, naturalLandscape, reverseDefault)
        orientationCheck?.cancel()
        orientationCheck = lifecycleScope.launch {
            // Recovery only: this timer never schedules or completes a visual handoff.
            delay(1_500)
            if (rotationBasis == basis && fullscreen == turned && !inPictureInPicture && !pipTransitioning &&
                measuredWindowRotation != target) {
                rotationUnavailable = true
                releaseRotationIfIdle()
            }
        }
        releaseRotationIfIdle()
    }

    private fun releaseRotationIfIdle() {
        val basis = rotationBasis ?: return
        if (!playerVisible && !fullscreen && rotationAnimationSettled && !pipTransitioning &&
            !inPictureInPicture && (measuredWindowRotation == basis.rotation || rotationUnavailable)) {
            orientationCheck?.cancel()
            rotationBasis = null
            measuredWindowRotation = null
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    private fun pipSupported() = hasPipSupport

    private fun pipAction(icon: Int) = RemoteAction(Icon.createWithResource(this, icon),
        getString(R.string.app_play_pause), getString(R.string.app_play_pause), pipToggleIntent)

    private fun pipParameters(): PictureInPictureParams {
        val state = playbackController.playerState.value
        val action = if (state.playWhenReady) pipPauseAction else pipPlayAction
        val ratio = state.videoAspectRatio.coerceIn(0.42f, 2.38f)
        return PictureInPictureParams.Builder().setAspectRatio(Rational((ratio * 10_000).roundToInt(), 10_000)).setActions(listOf(action)).apply {
            videoBounds?.let(::setSourceRectHint)
            setAutoEnterEnabled(settings.loaded.value && settings.preferences.value.autoPictureInPicture &&
                videoAlignedWithWindow && playerVisible && state.hasVideo && state.canRenderVideo && state.playWhenReady)
            setSeamlessResizeEnabled(true)
        }.build()
    }

    private fun updatePipParameters() {
        if (!pipSupported()) return
        val parameters = pipParameters()
        // Alignment changes need not change auto-enter (for example when disabled).
        // Avoid a synchronous WM call in the resize frame for an identical snapshot.
        if (parameters == publishedPipParameters) return
        runCatching { setPictureInPictureParams(parameters) }
            .onSuccess { publishedPipParameters = parameters }
    }

    private fun enterPip(showFailure: Boolean) {
        if (inPictureInPicture) return
        if (!pipSupported() || !playbackController.playerState.value.let { it.hasVideo && it.canRenderVideo }) {
            if (showFailure) Toast.makeText(this, R.string.app_pip_unavailable, Toast.LENGTH_SHORT).show()
            return
        }
        manualPipRequested = true
        immediatePipAlignment = false
        orientationCheck?.cancel()
        pipEntryPending = true
        pipTransitioning = true
        // The callback enters PiP after the live video has aligned with the current window.
    }

    private fun completePipEntry(showFailure: Boolean = true) {
        manualPipRequested = false
        // Explicit entry supplies its own parameters, bypassing the normal publisher.
        // Invalidate even on failure so a later update can retry the current snapshot.
        publishedPipParameters = null
        val accepted = pipSupported() && playbackController.playerState.value.hasVideo && playbackController.playerState.value.canRenderVideo &&
            runCatching { enterPictureInPictureMode(pipParameters()) }.getOrDefault(false)
        pipEntryPending = accepted
        if (!accepted) { pipTransitioning = false; immediatePipAlignment = false }
        if (!accepted && showFailure) Toast.makeText(this, R.string.app_pip_unavailable, Toast.LENGTH_SHORT).show()
    }

    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (settings.loaded.value && settings.preferences.value.autoPictureInPicture && playerVisible &&
            playbackController.playerState.value.let { it.playWhenReady && it.hasVideo && it.canRenderVideo }) {
            if (!videoAlignedWithWindow) {
                // Home can stop the Compose frame clock before a spring completes. Submit PiP
                // while foreground, and align its live window immediately instead of waiting.
                orientationCheck?.cancel()
                pipEntryPending = true
                pipTransitioning = true
                immediatePipAlignment = true
                completePipEntry(showFailure = false)
                return
            }
            pipEntryPending = true
            pipTransitioning = true
            lifecycleScope.launch {
                delay(1_200)
                if (started && !inPictureInPicture) { pipEntryPending = false; pipTransitioning = false }
            }
        }
    }

    override fun onPictureInPictureUiStateChanged(pipState: android.app.PictureInPictureUiState) {
        super.onPictureInPictureUiStateChanged(pipState)
        if (Build.VERSION.SDK_INT >= 35 && pipState.isTransitioningToPip) {
            pipEntryPending = true
            pipTransitioning = true
        }
    }

    override fun onPictureInPictureModeChanged(isInPictureInPictureMode: Boolean, newConfig: Configuration) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        inPictureInPicture = isInPictureInPictureMode
        immediatePipAlignment = false
        orientationCheck?.cancel()
        manualPipRequested = false
        pipEntryPending = false
        pipTransitioning = false
        if (!isInPictureInPictureMode && !started) checkStoppedPlayback()
    }

    override fun onStop() {
        super.onStop()
        playbackController.setVolumeUiActive(false)
        playbackController.cancelTemporarySpeed()
        started = false
        playbackController.setDiagnosticsVisible(false)
        checkStoppedPlayback()
    }

    private fun checkStoppedPlayback() {
        stoppedPlaybackCheck?.cancel()
        if (isChangingConfigurations || isInPictureInPictureMode) return
        stoppedPlaybackCheck = lifecycleScope.launch {
            if (pipEntryPending) delay(1_200)
            if (!started && !isInPictureInPictureMode && !isChangingConfigurations &&
                (!settings.loaded.value || !settings.preferences.value.backgroundPlayback)) {
                pipEntryPending = false
                pipTransitioning = false
                mediaSelection.cancelPendingPlayback()
                playbackController.pause()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        playbackController.setVolumeUiActive(true)
        started = true
        stoppedPlaybackCheck?.cancel()
        manualPipRequested = false
        immediatePipAlignment = false
        pipEntryPending = false
        pipTransitioning = false
        playbackController.setDiagnosticsVisible(diagnosticsPageVisible)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) playbackController.refreshSystemVolume()
        windowFocused = hasFocus
        if (hasFocus && playbackBrightness == null) systemBrightness =
            (android.provider.Settings.System.getInt(contentResolver,
                android.provider.Settings.System.SCREEN_BRIGHTNESS, 128) / 255f).coerceIn(0f, 1f)
        if (!hasFocus) playbackController.cancelTemporarySpeed()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("fullscreen", fullscreen)
        rotationBasis?.let {
            outState.putInt("rotation-basis", it.rotation)
            outState.putBoolean("rotation-basis-landscape", it.landscape)
        }
        playbackBrightness?.let { outState.putFloat("playback-brightness", it) }
        outState.putString("pending-diagnostics-export", pendingDiagnosticJson)
        super.onSaveInstanceState(outState)
    }

    override fun onKeyUp(keyCode: Int, event: android.view.KeyEvent): Boolean {
        val handled = super.onKeyUp(keyCode, event)
        if (keyCode in listOf(
                android.view.KeyEvent.KEYCODE_VOLUME_UP, android.view.KeyEvent.KEYCODE_VOLUME_DOWN,
                android.view.KeyEvent.KEYCODE_VOLUME_MUTE)) {
            window.decorView.post { playbackController.refreshSystemVolume(hardwareKey = true) }
        }
        return handled
    }

    override fun onDestroy() {
        unregisterReceiver(pipReceiver)
        super.onDestroy()
    }

    private companion object { const val PIP_TOGGLE_ACTION = "io.github.joyelliot.zivplayer.PIP_TOGGLE" }
}
