// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.content.Intent
import android.app.PendingIntent
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.provider.DocumentsContract
import android.view.WindowManager
import android.view.View
import android.view.ViewGroup
import android.view.TextureView
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.os.ParcelFileDescriptor
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.click
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionToken
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryFolderStatus
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.ShaderChain
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import io.github.joyelliot.zivplayer.data.media.toDiagnosticJson
import io.github.joyelliot.zivplayer.platform.playback.PlaybackDiagnosticsContract
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import io.github.joyelliot.zivplayer.platform.playback.SubtitleRequestContract
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Assume.assumeNotNull
import org.junit.Before
import org.junit.After
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Opt in with -e personalFixtureFolder NAME after granting the generated folder through SAF. */
class PersonalDevicePlaybackTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as ZivPlayerApplication
    private lateinit var folder: LibraryFolder
    private val configurationEvents = AtomicInteger()

    @Before fun requireGrantedFixtureFolder() {
        val name = InstrumentationRegistry.getArguments().getString("personalFixtureFolder")
        assumeNotNull(name)
        folder = runBlocking { app.mediaRepositories.library.folders().single { it.name == name } }
        onMain { activity.activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
    }

    @After fun clearFixtureWindowFlag() {
        onMain { activity.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
    }

    @Test fun formatMatrixPlaysThroughGrantedDocumentsAndExportsDiagnostics() = withController { controller ->
        val samples = listOf(
            Sample("avc-1080p50mbps.mp4", "h264", "aac", 1920, 1080),
            Sample("hevc-main10-flac.mkv", "hevc", "flac", 1920, 1080),
            Sample("av1-opus.webm", "av1", "opus", 1920, 1080),
            Sample("hevc-2160p30.mkv", "hevc", "aac", 3840, 2160),
            Sample("hdr-pq-main10.mkv", "hevc", "aac", 1920, 1080, "pq"),
            Sample("hdr-hlg-main10.mkv", "hevc", "aac", 1920, 1080, "hlg"),
            Sample("audio-opus.opus", null, "opus"),
            Sample("audio-flac.flac", null, "flac"),
            Sample("audio-pcm.wav", null, "pcm_s16le"),
        )
        samples.forEach { sample ->
            open(controller, sample.name)
            onMain { controller.play() }
            await(controller, "${sample.name} advancing") { isPlaying && currentPosition > 400 }
            val command = SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY)
            assertTrue(onMain { controller.isSessionCommandAvailable(command) })
            var result: SessionResult? = null
            val deadline = SystemClock.elapsedRealtime() + 5_000
            while (SystemClock.elapsedRealtime() < deadline) {
                val read = onMain { controller.sendCustomCommand(command, Bundle.EMPTY) }.get(5, TimeUnit.SECONDS)
                if (read.resultCode == SessionResult.RESULT_SUCCESS && read.extras.getString("audioCodec") != null &&
                    (sample.video == null || read.extras.containsKey("videoWidth"))) { result = read; break }
                SystemClock.sleep(80)
            }
            val observed = checkNotNull(result) { "${sample.name}: diagnostics never became available" }
            assertNull("Playback configuration failed", observed.extras.getString(PlaybackDiagnosticsContract.SETTINGS_MESSAGE))
            val diagnostics = PlaybackDiagnosticsContract.decode(observed.extras)
            File(app.cacheDir, "format-${sample.name}.json").writeText(diagnostics.toDiagnosticJson(System.currentTimeMillis(), System.currentTimeMillis()))
            assertTrue("${sample.name}: ${diagnostics.audioCodec}", codecMatches(diagnostics.audioCodec, sample.audio))
            if (sample.video == null) {
                assertNull(diagnostics.videoCodec)
                assertNull(diagnostics.videoWidth)
                assertTrue(onMain { controller.currentTracks.groups.none { it.type == C.TRACK_TYPE_VIDEO } })
            } else {
                assertTrue("${sample.name}: ${diagnostics.videoCodec}", codecMatches(diagnostics.videoCodec, sample.video))
                assertEquals(sample.width, diagnostics.videoWidth)
                assertEquals(sample.height, diagnostics.videoHeight)
                sample.transfer?.let { assertEquals(it, diagnostics.transferFunction) }
            }
            onMain { controller.pause() }
            await(controller, "${sample.name} paused") { !isPlaying && !playWhenReady }
            capture("format-${sample.name}.png")
            onMain { controller.seekTo(1_000) }
            await(controller, "${sample.name} seek") { currentPosition in 800..1_250 }
            onMain { controller.stop() }
            await(controller, "${sample.name} stopped") { playbackState == Player.STATE_IDLE }
        }
    }

    @Test fun libraryRescanAndInterruptedScanPreserveFlagsAndHistory() {
        val repository = app.mediaRepositories.library
        runBlocking { app.documentTreeLibrary.scan(folder.id) }
        val before = runBlocking { repository.observeMedia().first().filter { it.folderId == folder.id } }
        assertTrue(before.size >= 9)
        val selected = before.single { it.title == "audio-flac.flac" }
        val history = runBlocking { app.recentMediaRepository.observeRecentlyOpened(200).first() }
        try {
            runBlocking {
                repository.setFavorite(folder.id, selected.sourceUri, true)
                repository.setHidden(folder.id, selected.sourceUri, true)
                app.documentTreeLibrary.scan(folder.id)
            }
            val rescanned = runBlocking { repository.observeMedia().first().single { it.sourceUri == selected.sourceUri } }
            assertTrue(rescanned.favorite && rescanned.hidden)
            try {
                runBlocking { app.documentTreeLibrary.scan(folder.id) { throw CancellationException("Intentional partial scan") } }
                fail("Expected scan cancellation")
            } catch (_: CancellationException) { }
            assertEquals(LibraryFolderStatus.INTERRUPTED, runBlocking { repository.folder(folder.id) }?.status)
            assertEquals(before.map { it.sourceUri }.toSet(), runBlocking { repository.observeMedia().first().filter { it.folderId == folder.id }.map { it.sourceUri }.toSet() })
            assertEquals(history, runBlocking { app.recentMediaRepository.observeRecentlyOpened(200).first() })
        } finally {
            runBlocking {
                repository.setFavorite(folder.id, selected.sourceUri, selected.favorite)
                repository.setHidden(folder.id, selected.sourceUri, selected.hidden)
                app.documentTreeLibrary.scan(folder.id)
            }
        }
    }

    @Test fun fullscreenPictureInPictureAndBackgroundPolicies() = withController { controller ->
        val original = runBlocking { app.playerPreferences.preferences.first() }
        val repeat = onMain { controller.repeatMode }
        fun foregroundPlayer() {
            DeviceForegroundRule.startActivity("${app.packageName}/.MainActivity")
            awaitCondition("return from PiP") { onMain { !activity.activity.isInPictureInPictureMode } }
            awaitCondition("player activity resumed") { onMain { activity.activity.lifecycle.currentState == Lifecycle.State.RESUMED } }
            activity.waitForIdle()
        }
        try {
            updateConfiguration(controller) { it.copy(backgroundPlayback = true, autoPictureInPicture = false) }
            open(controller, "avc-1080p50mbps.mp4")
            onMain { controller.repeatMode = Player.REPEAT_MODE_ONE; controller.play() }
            await(controller, "video playing") { isPlaying && currentPosition > 400 }
            activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_rotate)).performClick()
            awaitCondition("landscape fullscreen") {
                onMain { activity.activity.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE }
            }
            await(controller, "fullscreen preserves playback") { isPlaying }
            captureVideoFrame("fullscreen-video.png")
            capture("fullscreen-landscape.png")
            activity.activityRule.scenario.recreate()
            await(controller, "fullscreen recreation preserves playback") { isPlaying }
            captureVideoFrame("fullscreen-recreated-video.png")
            revealPlayerControls()
            activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_rotate)).performClick()
            awaitCondition("portrait player") {
                onMain { activity.activity.resources.configuration.orientation == Configuration.ORIENTATION_PORTRAIT }
            }
            activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_quick_menu)).performClick()
            activity.onNodeWithText(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_pip)).performScrollTo().performClick()
            awaitCondition("manual PiP entered") { onMain { activity.activity.isInPictureInPictureMode } }
            await(controller, "manual PiP keeps playback") { isPlaying }
            SystemClock.sleep(2_000) // The mode callback can precede the system's resize transition.
            captureVideoFrame("manual-pip-video.png")
            capture("manual-pip.png")
            val toggle = onMain { PendingIntent.getBroadcast(app, 1,
                Intent("io.github.joyelliot.zivplayer.PIP_TOGGLE").setPackage(app.packageName),
                PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE) }
            assertNotNull("The PiP action must have an existing PendingIntent", toggle)
            checkNotNull(toggle).send()
            await(controller, "PiP action pauses") { !isPlaying && !playWhenReady }
            assertTrue(onMain { activity.activity.isInPictureInPictureMode })
            toggle.send()
            await(controller, "PiP action resumes") { isPlaying }
            foregroundPlayer()
            await(controller, "PiP return keeps playback") { isPlaying }

            updateConfiguration(controller) { it.copy(backgroundPlayback = false, autoPictureInPicture = false) }
            activity.waitForIdle()
            SystemClock.sleep(500)
            shell("input keyevent KEYCODE_HOME")
            await(controller, "background disabled pauses") { !playWhenReady && !isPlaying }
            assertFalse(onMain { activity.activity.isInPictureInPictureMode })
            foregroundPlayer()

            updateConfiguration(controller) { it.copy(backgroundPlayback = true) }
            onMain { controller.play() }
            await(controller, "foreground resumed") { isPlaying }
            activity.waitForIdle()
            SystemClock.sleep(500)
            shell("input keyevent KEYCODE_HOME")
            SystemClock.sleep(700)
            await(controller, "background enabled keeps playback") { isPlaying }
            assertFalse(onMain { activity.activity.isInPictureInPictureMode })
            foregroundPlayer()

            updateConfiguration(controller) { it.copy(backgroundPlayback = false, autoPictureInPicture = true) }
            activity.waitForIdle()
            // Start the Home transition away from the short fixture's repeat/load boundary.
            await(controller, "steady video before automatic PiP") { isPlaying && currentPosition in 1_000..2_000 }
            val ui = onMain { ViewModelProvider(activity.activity)[PlaybackControllerViewModel::class.java] }
            val settings = onMain { ViewModelProvider(activity.activity)[SettingsViewModel::class.java] }
            awaitCondition("automatic PiP UI prerequisites") { onMain {
                ui.playerState.value.let { it.playWhenReady && it.hasVideo && it.canRenderVideo } &&
                    settings.loaded.value && settings.preferences.value.autoPictureInPicture
            } }
            activity.waitForIdle()
            File(app.cacheDir, "automatic-pip-before-home.txt").writeText(onMain {
                "state=${ui.playerState.value}\npreferences=${settings.preferences.value}\n"
            })
            shell("input keyevent KEYCODE_HOME")
            awaitCondition("automatic PiP entered") { onMain { activity.activity.isInPictureInPictureMode } }
            await(controller, "automatic PiP keeps playback with background disabled") { isPlaying }
            SystemClock.sleep(2_000)
            captureVideoFrame("automatic-pip-video.png")
            capture("automatic-pip.png")
            foregroundPlayer()

            open(controller, "audio-flac.flac")
            onMain { controller.play() }
            await(controller, "pure audio playing") { isPlaying && currentPosition > 400 }
            revealPlayerControls()
            activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_quick_menu)).performClick()
            activity.onNodeWithText(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_pip)).performScrollTo().assertIsNotEnabled()
            activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_close_menu)).performClick()
            shell("input keyevent KEYCODE_HOME")
            await(controller, "pure audio respects background disabled") { !isPlaying && !playWhenReady }
            assertFalse(onMain { activity.activity.isInPictureInPictureMode })
        } finally {
            foregroundPlayer()
            revealPlayerControls()
            activity.onAllNodesWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.library.R.string.library_back_folders)).fetchSemanticsNodes()
                .takeIf { it.isNotEmpty() }?.let {
                    activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.library.R.string.library_back_folders)).performClick()
                }
            onMain { activity.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED }
            onMain { controller.repeatMode = repeat }
            updateConfiguration(controller) { original }
        }
    }

    /** Explicit short-fixture switching stress; this does not substitute for a long movie. */
    @Test fun generatedFormatSwitchingStress() {
        val seconds = InstrumentationRegistry.getArguments().getString("stressSeconds")?.toIntOrNull()
        assumeNotNull(seconds)
        require(checkNotNull(seconds) in 30..600)
        withController { controller ->
            val previousRepeat = onMain { controller.repeatMode }
            val names = listOf("avc-1080p50mbps.mp4", "hevc-main10-flac.mkv", "av1-opus.webm",
                "hevc-2160p30.mkv", "hdr-pq-main10.mkv", "hdr-hlg-main10.mkv",
                "audio-opus.opus", "audio-flac.flac", "audio-pcm.wav")
            val output = File(app.cacheDir, "stress-diagnostics.jsonl")
            output.writeText("")
            val started = SystemClock.elapsedRealtime()
            var activeIndex = -1
            try {
                onMain { controller.repeatMode = Player.REPEAT_MODE_ONE }
                while (SystemClock.elapsedRealtime() - started < seconds * 1_000L) {
                    val index = ((SystemClock.elapsedRealtime() - started) / 20_000).toInt() % names.size
                    if (index != activeIndex) {
                        open(controller, names[index])
                        onMain { controller.play() }
                        activeIndex = index
                    }
                    await(controller, "stress ${names[index]} advancing") { isPlaying && currentPosition > 200 }
                    val sampledAt = System.currentTimeMillis()
                    val result = onMain { controller.sendCustomCommand(
                        SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY), Bundle.EMPTY,
                    ) }.get(5, TimeUnit.SECONDS)
                    assertEquals(SessionResult.RESULT_SUCCESS, result.resultCode)
                    assertNull(result.extras.getString(PlaybackDiagnosticsContract.SETTINGS_MESSAGE))
                    output.appendText(PlaybackDiagnosticsContract.decode(result.extras)
                        .toDiagnosticJson(sampledAt, System.currentTimeMillis()).replace("\n", "") + "\n")
                    SystemClock.sleep(1_000)
                }
                File(app.cacheDir, "stress-completed.txt").writeText("elapsedMs=${SystemClock.elapsedRealtime() - started}\n")
            } finally { onMain { controller.repeatMode = previousRepeat } }
        }
    }

    @Test fun importedFontAndShaderApplyDuringPlayback() = withController { controller ->
        val original = runBlocking { app.playerPreferences.preferences.first() }
        val existingResources = runBlocking { app.mediaRepositories.resources.resources.first().map { it.id }.toSet() }
        val imported = mutableListOf<io.github.joyelliot.zivplayer.core.model.PlaybackResourceId>()
        try {
            updateConfiguration(controller) { it.copy(defaultPlaybackRate = PlaybackRatePermille(1250),
                options = PlaybackOptions(subtitleScalePercent = 150, overrideStyledSubtitles = true)) }
            val opened = open(controller, "tracks-off.mkv")
            onMain { controller.play() }
            await(controller, "stored default speed applies on open") { isPlaying && playbackParameters.speed == 1.25f }
            val subtitle = onMain { controller.sendCustomCommand(
                SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY), Bundle().apply {
                    putString(SubtitleRequestContract.URI, document("external.srt").toString())
                    putString(SubtitleRequestContract.MEDIA_ID, opened.first.value)
                    putLong(SubtitleRequestContract.REQUEST_SEQUENCE, opened.second)
                },
            ) }.get(15, TimeUnit.SECONDS)
            assertEquals(SessionResult.RESULT_SUCCESS, subtitle.resultCode)
            await(controller, "resource test external subtitle") { currentTracks.groups.any {
                it.type == C.TRACK_TYPE_TEXT && it.isSelected && it.getTrackFormat(0).label == "external.srt"
            } }
            onMain { controller.pause(); controller.seekTo(2_000) }
            await(controller, "resource test fixed frame") { !isPlaying && currentPosition in 1_800..2_300 }
            capture("resource-font-default.png")
            val font = runBlocking { app.mediaRepositories.resources.import(document("NotoSerif-Regular.ttf"), PlaybackResourceKind.FONT) }
            imported += font.id
            assertEquals("Noto Serif", font.fontFamily)
            assertTrue(font.sizeBytes > 0)
            updateConfiguration(controller) { it.copy(options = it.options.copy(subtitleFont = font.id, subtitleFontFamily = font.fontFamily)) }
            capture("resource-font-noto-serif.png")
            val normal = captureVideoFrame("resource-video-normal.png")

            val shader = runBlocking { app.mediaRepositories.resources.import(document("test-invert.glsl"), PlaybackResourceKind.SHADER) }
            imported += shader.id
            assertTrue(shader.sizeBytes > 0)
            // Importing another kind must preserve existing records and files during reconciliation.
            val fontPath = runBlocking { app.mediaRepositories.resources.resolve(font.id, PlaybackResourceKind.FONT) }
            assertTrue(fontPath.isFile)
            val fontEntry = File(app.filesDir, "playback-resources/font/${font.id.value}.${font.extension}")
            File(app.cacheDir, "resource-paths.txt").writeText("entry=${fontEntry.absolutePath}\ncanonical=${fontEntry.canonicalPath}\nresolved=${fontPath.absolutePath}\n")
            updateConfiguration(controller) { it.copy(options = it.options.copy(shaders = ShaderChain(listOf(shader.id)))) }
            val inverted = captureVideoFrame("resource-video-inverted.png")
            assertTrue("Shader must visibly change the fixed video frame", frameDifference(normal, inverted) > 40)
            capture("resource-shader-inverted.png")
            updateConfiguration(controller) { it.copy(options = it.options.copy(shaders = ShaderChain())) }
            val disabled = captureVideoFrame("resource-video-disabled.png")
            assertTrue("Disabling the shader must restore the same frame", frameDifference(normal, disabled) < 4)
            capture("resource-shader-disabled.png")
            File(app.cacheDir, "resource-imports.txt").writeText("font=${font.id.value}, family=${font.fontFamily}\nshader=${shader.id.value}\n")
        } finally {
            updateConfiguration(controller) { original }
            runBlocking { imported.distinct().filterNot { it in existingResources }.forEach { app.deletePlaybackResource(it) } }
        }
    }

    /** Run separately, then force-stop the target before verifyPersistentChoicesAfterProcessRestart. */
    @Test fun preparePersistentChoices() = withController { controller ->
        File(app.cacheDir, "persistent-choices-process.txt").delete()
        listOf("tracks-off.mkv", "tracks-external.mkv").forEach { name ->
            // These two files belong to the explicitly granted generated fixture folder.
            // Clear old choices before loading so a prior run cannot satisfy this run's save check.
            val fixture = runBlocking { app.mediaDocumentRegistrar.open(document(name), persistableReadOffered = false) }
            runBlocking { app.mediaRepositories.playbackPreferences.clear(fixture.mediaId) }
            val opened = open(controller, name)
            await(controller, "two audio tracks") { currentTracks.groups.count { it.type == C.TRACK_TYPE_AUDIO } == 2 }
            onMain {
                val audio = controller.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }[1]
                controller.trackSelectionParameters = controller.trackSelectionParameters.buildUpon()
                    .setOverrideForType(TrackSelectionOverride(audio.mediaTrackGroup, 0))
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build()
            }
            await(controller, "second audio and subtitle off") {
                currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }[1].isSelected &&
                    currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected }
            }
            if (name == "tracks-external.mkv") {
                val subtitleUri = document("external.srt")
                val result = onMain {
                    controller.sendCustomCommand(SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY), Bundle().apply {
                        putString(SubtitleRequestContract.URI, subtitleUri.toString())
                        putString(SubtitleRequestContract.MEDIA_ID, opened.first.value)
                        putLong(SubtitleRequestContract.REQUEST_SEQUENCE, opened.second)
                    })
                }.get(15, TimeUnit.SECONDS)
                assertEquals(result.extras.toString(), SessionResult.RESULT_SUCCESS, result.resultCode)
                await(controller, "external subtitle selected") { currentTracks.groups.any {
                    it.type == C.TRACK_TYPE_TEXT && it.isSelected && it.getTrackFormat(0).label?.contains("external.srt") == true
                } }
            }
            awaitSavedChoice(opened.first, name == "tracks-off.mkv")
        }
        File(app.cacheDir, "persistent-choices-process.txt").writeText(processIdentity())
    }

    @Test fun verifyPersistentChoicesAfterProcessRestart() = withController { controller ->
        val marker = File(app.cacheDir, "persistent-choices-process.txt")
        assertTrue("Run preparePersistentChoices first", marker.isFile)
        assertNotEquals("Force-stop the target between prepare and verify", marker.readText(), processIdentity())
        listOf("tracks-off.mkv", "tracks-external.mkv").forEach { name ->
            val opened = open(controller, name)
            awaitSavedChoice(opened.first, name == "tracks-off.mkv")
            onMain { controller.play() }
            await(controller, "$name restores selections before advancing") {
                isPlaying && currentPosition > 400 &&
                    currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }.getOrNull(1)?.isSelected == true &&
                    if (name == "tracks-off.mkv") currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected }
                    else currentTracks.groups.any { it.type == C.TRACK_TYPE_TEXT && it.isSelected && it.getTrackFormat(0).label?.contains("external.srt") == true }
            }
            onMain { controller.pause(); controller.seekTo(2_000) }
            await(controller, "persisted selection seek") { !isPlaying && currentPosition in 1_800..2_300 }
            capture("restored-$name.png")
        }
        assertTrue(File(app.cacheDir, "persistent-choices-process.txt").delete())
    }

    private fun awaitSavedChoice(id: MediaId, off: Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 5_000
        while (SystemClock.elapsedRealtime() < deadline) {
            val saved = runBlocking { app.mediaRepositories.playbackPreferences.find(id) }
            if (saved.audio?.index == 1 && if (off) saved.subtitlesDisabled else saved.subtitle?.external == true && saved.externalSubtitles.isNotEmpty()) return
            SystemClock.sleep(50)
        }
        error("Playback preferences did not persist for $id")
    }

    private fun open(controller: MediaController, name: String): Pair<MediaId, Long> {
        val uri = document(name)
        val opened = runBlocking { app.mediaDocumentRegistrar.open(uri, persistableReadOffered = false) }
        assertTrue("Real tree document must be restart-safe", opened.isRestartSafe)
        val sequence = PlaybackRequestSequencer.next()
        onMain {
            controller.setMediaItem(MediaItem.Builder().setMediaId(opened.mediaId.value).setUri(uri)
                .setMediaMetadata(MediaMetadata.Builder().setTitle(name).setExtras(Bundle().apply {
                    putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                }).build()).build())
            controller.prepare()
        }
        await(controller, "$name ready") { playbackState == Player.STATE_READY && currentMediaItem?.mediaId == opened.mediaId.value }
        // The folder home exposes ongoing playback through its persistent mini player.
        activity.waitForIdle()
        if (activity.onAllNodesWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_video_area)).fetchSemanticsNodes().isEmpty()) {
            activity.waitUntil(5_000) { activity.onAllNodesWithText(name).fetchSemanticsNodes().isNotEmpty() }
            activity.onNodeWithText(name).performClick()
        }
        return opened.mediaId to sequence
    }

    private fun document(name: String): Uri {
        val tree = Uri.parse(folder.sourceUri.value)
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        app.contentResolver.query(children, arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)!!.use { cursor ->
            while (cursor.moveToNext()) if (cursor.getString(1) == name)
                return DocumentsContract.buildDocumentUriUsingTree(tree, cursor.getString(0))
        }
        error("Prepare $name in the granted test folder")
    }

    private fun withController(block: (MediaController) -> Unit) {
        val controller = onMain {
            MediaController.Builder(app, SessionToken(app, ComponentName(app, PlaybackService::class.java)))
                .setListener(object : MediaController.Listener {
                    override fun onCustomCommand(controller: MediaController, command: SessionCommand, args: Bundle): ListenableFuture<SessionResult> {
                        if (command.customAction == PlaybackDiagnosticsContract.ACTION_SETTINGS_STATUS) {
                            configurationEvents.incrementAndGet()
                            return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
                        }
                        return super.onCustomCommand(controller, command, args)
                    }
                }).buildAsync()
        }.get(15, TimeUnit.SECONDS)
        activity.waitForIdle()
        try { block(controller) } finally { onMain { controller.stop(); controller.release() } }
    }

    private fun revealPlayerControls() {
        val menu = app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_quick_menu)
        if (activity.onAllNodesWithContentDescription(menu).fetchSemanticsNodes().isEmpty()) {
            activity.onNodeWithContentDescription(app.getString(io.github.joyelliot.zivplayer.feature.player.R.string.player_video_area))
                .performTouchInput { click(Offset(width * 0.5f, height * 0.35f)) }
            activity.waitUntil(2_000) { activity.onAllNodesWithContentDescription(menu).fetchSemanticsNodes().isNotEmpty() }
        }
    }

    private fun capture(name: String) {
        activity.waitForIdle()
        SystemClock.sleep(250)
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        try { File(app.cacheDir, name).outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
    }

    /** Reads native video pixels directly from the texture, independent of player state. */
    private fun captureVideoFrame(name: String): List<Int> {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        while (SystemClock.elapsedRealtime() < deadline) {
            val surface = onMain { findSurface(activity.activity.window.decorView) }
            val bitmap = surface?.let { onMain { if (it.isAvailable) it.bitmap else null } }
            if (bitmap != null) {
                try {
                        val samples = (1..9).flatMap { x -> (1..3).map { y -> bitmap.getPixel(bitmap.width * x / 10, bitmap.height * y / 4) } }
                        File(app.cacheDir, name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                        if (samples.count { pixel ->
                            val rgb = listOf(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
                            rgb.max() - rgb.min() > 60 && rgb.max() > 90
                        } >= 6) return samples
                } finally { bitmap.recycle() }
            }
            SystemClock.sleep(250)
        }
        error("No colorful native video frame for $name")
    }

    private fun findSurface(view: View): TextureView? = when (view) {
        is TextureView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { findSurface(view.getChildAt(it)) }
        else -> null
    }

    private fun frameDifference(first: List<Int>, second: List<Int>): Double = first.zip(second).map { (a, b) ->
        (kotlin.math.abs(Color.red(a) - Color.red(b)) + kotlin.math.abs(Color.green(a) - Color.green(b)) +
            kotlin.math.abs(Color.blue(a) - Color.blue(b))) / 3.0
    }.average()

    private fun await(controller: MediaController, description: String, predicate: MediaController.() -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        while (SystemClock.elapsedRealtime() < deadline) {
            if (onMain { check(controller.playerError == null) { "$description: ${controller.playerError}" }; controller.predicate() }) return
            SystemClock.sleep(50)
        }
        val state = onMain {
            "state=${controller.playbackState}, position=${controller.currentPosition}, " +
                "playing=${controller.isPlaying}, playWhenReady=${controller.playWhenReady}, tracks=" +
                controller.currentTracks.groups.joinToString { group ->
                    val format = group.getTrackFormat(0)
                    "[type=${group.type}, selected=${group.isSelected}, id=${format.id}, " +
                        "lang=${format.language}, label=${format.label}, codec=${format.codecs}]"
                }
        }
        val diagnostics = onMain { controller.sendCustomCommand(
            SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY), Bundle.EMPTY,
        ) }.get(5, TimeUnit.SECONDS)
        val settings = diagnostics.extras.getString(PlaybackDiagnosticsContract.SETTINGS_MESSAGE)
        error("$description timed out: $state, settingsMessage=$settings")
    }

    private fun processIdentity() = "${android.os.Process.myPid()}:${android.os.Process.getStartElapsedRealtime()}"

    private fun updateConfiguration(controller: MediaController, transform: (PlayerPreferences) -> PlayerPreferences) {
        val original = runBlocking { app.playerPreferences.preferences.first() }
        val target = transform(original)
        if (target == original) return
        val generation = configurationEvents.get()
        runBlocking { withTimeout(15_000) {
            app.playerPreferences.update { target }
            assertEquals("Stored preferences were normalized unexpectedly", target, app.playerPreferences.preferences.first())
        }
        }
        awaitCondition("native configuration acknowledgement") { configurationEvents.get() > generation }
        val result = onMain { controller.sendCustomCommand(
            SessionCommand(PlaybackDiagnosticsContract.ACTION_READ, Bundle.EMPTY), Bundle.EMPTY,
        ) }.get(5, TimeUnit.SECONDS)
        assertEquals(SessionResult.RESULT_SUCCESS, result.resultCode)
        assertNull(result.extras.getString(PlaybackDiagnosticsContract.SETTINGS_MESSAGE))
    }

    private fun shell(command: String): String = ParcelFileDescriptor.AutoCloseInputStream(
        instrumentation.uiAutomation.executeShellCommand(command),
    ).bufferedReader().use { it.readText() }

    private fun awaitCondition(description: String, predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        while (SystemClock.elapsedRealtime() < deadline) {
            if (predicate()) return
            SystemClock.sleep(50)
        }
        error("$description timed out")
    }

    // mpv's codec properties are human-readable decoder descriptions, including punctuation.
    private fun codecMatches(observed: String?, expected: String): Boolean {
        val normalized = observed.orEmpty().lowercase().replace(Regex("[^a-z0-9]"), "")
        return when (expected) {
            "pcm_s16le" -> "pcms16le" in normalized || "pcmsigned16bitlittleendian" in normalized
            else -> expected in normalized
        }
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }

    private data class Sample(val name: String, val video: String?, val audio: String,
        val width: Int? = null, val height: Int? = null, val transfer: String? = null)
}
