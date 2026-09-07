// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.WindowManager
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionToken
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import io.github.joyelliot.zivplayer.platform.playback.SubtitleRequestContract
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Real service/JNI/codec smoke. Frame quality, audible output and device controls also need visual acceptance. */
class NativePlaybackSmokeTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test
    fun sourceNativePlaybackTracksSubtitlesReplayAndSurfaceRecreation() {
        val context = instrumentation.targetContext
        onMain { activity.activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
        context.contentResolver.openFileDescriptor(fixture("tracks-subtitles.mkv"), "r").use {
            check(it != null && it.statSize > 0) { "The generated test fixture is not readable." }
        }
        val future = onMain {
            MediaController.Builder(context, SessionToken(context, ComponentName(context, PlaybackService::class.java)))
                .buildAsync()
        }
        val controller = future.get(15, TimeUnit.SECONDS)
        activity.waitForIdle()
        activity.onNodeWithText(context.getString(R.string.app_player)).performClick()
        val sequence = PlaybackRequestSequencer.next()
        val mediaId = "zivplayer-generated-device-fixture"
        try {
            onMain {
                val commands = controller.availableCommands
                val description = (0 until commands.size()).map(commands::get).toString()
                check(controller.isCommandAvailable(Player.COMMAND_SET_MEDIA_ITEM)) {
                    "Missing COMMAND_SET_MEDIA_ITEM; available=$description"
                }
                check(controller.isCommandAvailable(Player.COMMAND_PREPARE)) {
                    "Missing COMMAND_PREPARE; available=$description"
                }
                controller.setMediaItem(MediaItem.Builder().setMediaId(mediaId)
                    .setUri(fixture("tracks-subtitles.mkv"))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Generated native smoke fixture")
                        .setExtras(Bundle().apply {
                            putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                            putString(PlaybackRequestMetadata.TOKEN_EXTRA, "native-smoke-$sequence")
                        }).build()).build())
                controller.prepare()
            }
            await(controller, "two audio and two embedded subtitle tracks") {
                currentTracks.groups.count { it.type == C.TRACK_TYPE_AUDIO } == 2 &&
                    currentTracks.groups.count { it.type == C.TRACK_TYPE_TEXT } == 2
            }
            onMain { controller.play() }
            await(controller, "advancing decoded playback") { isPlaying && currentPosition >= 600 }
            onMain { controller.pause() }
            await(controller, "pause") { !playWhenReady && !isPlaying }
            onMain { controller.seekTo(4_000) }
            await(controller, "seek") { currentPosition in 3_800..4_500 }
            captureScreen("native-embedded-ass.png")
            onMain {
                controller.setPlaybackSpeed(1.25f)
                val second = controller.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }[1]
                controller.trackSelectionParameters = controller.trackSelectionParameters.buildUpon()
                    .setOverrideForType(TrackSelectionOverride(second.mediaTrackGroup, 0))
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build()
            }
            await(controller, "audio selection and subtitles off") {
                currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }[1].isSelected &&
                    currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected }
            }
            onMain {
                val embeddedSrt = controller.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }[1]
                controller.trackSelectionParameters = controller.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(TrackSelectionOverride(embeddedSrt.mediaTrackGroup, 0)).build()
            }
            await(controller, "embedded SRT selected") {
                currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }[1].isSelected
            }
            captureScreen("native-embedded-srt.png")
            listOf("external.ass", "external.srt").forEachIndexed { index, name ->
                val subtitleResult = onMain {
                    controller.sendCustomCommand(SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY), Bundle().apply {
                        putString(SubtitleRequestContract.URI, fixture(name).toString())
                        putString(SubtitleRequestContract.MEDIA_ID, mediaId)
                        putLong(SubtitleRequestContract.REQUEST_SEQUENCE, sequence)
                    })
                }.get(15, TimeUnit.SECONDS)
                assertEquals(subtitleResult.extras.toString(), SessionResult.RESULT_SUCCESS, subtitleResult.resultCode)
                await(controller, "$name selected") {
                    currentTracks.groups.count { it.type == C.TRACK_TYPE_TEXT } == 3 + index &&
                        currentTracks.groups.any { it.type == C.TRACK_TYPE_TEXT && it.isSelected &&
                            it.getTrackFormat(0).label?.contains(name) == true }
                }
                captureScreen("native-${name.replace('.', '-')}.png")
            }
            onMain { controller.trackSelectionParameters = controller.trackSelectionParameters.buildUpon()
                .clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build() }
            await(controller, "explicit subtitle off") {
                currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected }
            }
            onMain { controller.stop() }
            await(controller, "stop") { playbackState == Player.STATE_IDLE }
            onMain { controller.play() }
            await(controller, "replay preserves tracks and subtitle off") {
                isPlaying && currentPosition > 300 &&
                    currentTracks.groups.count { it.type == C.TRACK_TYPE_TEXT } == 4 &&
                    currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }.getOrNull(1)?.isSelected == true &&
                    currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected }
            }
            activity.activityRule.scenario.recreate()
            onMain { activity.activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
            await(controller, "playback after Activity and Surface recreation") { isPlaying && currentPosition > 800 }
            captureScreen("native-recreated-surface.png")
            assertTrue(onMain { controller.playbackParameters.speed == 1.25f })
        } finally {
            onMain { controller.stop(); controller.release() }
        }
    }

    /** Kept separate so an explicit OEM focus-policy exception does not skip native playback coverage. */
    @Test
    fun realTransientAudioFocusPausesAndResumes() {
        val context = instrumentation.targetContext
        onMain { activity.activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
        val controller = onMain {
            MediaController.Builder(context, SessionToken(context, ComponentName(context, PlaybackService::class.java))).buildAsync()
        }.get(15, TimeUnit.SECONDS)
        val playWhenReadyReason = AtomicInteger()
        try {
            onMain {
                controller.addListener(object : Player.Listener {
                    override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                        playWhenReadyReason.set(reason)
                    }
                })
                val sequence = PlaybackRequestSequencer.next()
                controller.setMediaItem(MediaItem.Builder().setMediaId("zivplayer-focus-fixture")
                    .setUri(fixture("tracks-subtitles.mkv"))
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Audio focus test")
                        .setExtras(Bundle().apply { putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence) }).build()).build())
                controller.prepare()
            }
            await(controller, "focus fixture ready") { playbackState == Player.STATE_READY }
            onMain { controller.play() }
            await(controller, "focus fixture playing") { isPlaying && currentPosition > 300 }
            val interruptedPosition = onMain { controller.currentPosition }
            try {
                DeviceForegroundRule.startActivity("${context.packageName}.test/io.github.joyelliot.zivplayer.FocusInterruptionActivity")
                await(controller, "real OS transient audio focus loss pauses playback") {
                    !playWhenReady && !isPlaying &&
                        playWhenReadyReason.get() == Player.PLAY_WHEN_READY_CHANGE_REASON_AUDIO_FOCUS_LOSS
                }
            } finally {
                DeviceForegroundRule.startActivity("${context.packageName}/.MainActivity")
            }
            await(controller, "real OS focus return resumes playback") {
                isPlaying && currentPosition > interruptedPosition + 200
            }
        } finally {
            onMain { controller.stop(); controller.release() }
        }
    }

    private fun captureScreen(name: String) {
        activity.waitForIdle()
        // Native Surface rendering settles independently of Compose's idle state.
        SystemClock.sleep(250)
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        try {
            File(instrumentation.targetContext.cacheDir, name).outputStream().use {
                check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
            }
        } finally {
            bitmap.recycle()
        }
    }

    private fun await(controller: MediaController, description: String, predicate: MediaController.() -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        while (SystemClock.elapsedRealtime() < deadline) {
            val done = onMain {
                check(controller.playerError == null) { "$description: ${controller.playerError}" }
                controller.predicate()
            }
            if (done) return
            SystemClock.sleep(50)
        }
        error("Timed out waiting for $description; " + onMain {
            "state=${controller.playbackState}, position=${controller.currentPosition}, tracks=${controller.currentTracks.groups}, " +
                "mediaId=${controller.currentMediaItem?.mediaId}, sequence=${PlaybackRequestSequencer.current()}, " +
                "commands=${(0 until controller.availableCommands.size()).map(controller.availableCommands::get)}"
        })
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }

    private fun fixture(name: String): Uri = Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/$name")
}
