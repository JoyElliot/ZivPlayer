// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
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
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import io.github.joyelliot.zivplayer.platform.playback.SubtitleRequestContract
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.TimeUnit

/** Real service/JNI/codec smoke. Frame quality, audible output and device controls also need visual acceptance. */
class NativePlaybackSmokeTest {
    @get:Rule val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test
    fun sourceNativePlaybackTracksSubtitlesReplayAndSurfaceRecreation() {
        val context = instrumentation.targetContext
        val future = onMain {
            MediaController.Builder(context, SessionToken(context, ComponentName(context, PlaybackService::class.java)))
                .buildAsync()
        }
        val controller = future.get(15, TimeUnit.SECONDS)
        val sequence = PlaybackRequestSequencer.next()
        val mediaId = "zivplayer-generated-device-fixture"
        try {
            onMain {
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
            val subtitleResult = onMain {
                controller.sendCustomCommand(SessionCommand(SubtitleRequestContract.ACTION_ADD, Bundle.EMPTY), Bundle().apply {
                    putString(SubtitleRequestContract.URI, fixture("external.ass").toString())
                    putString(SubtitleRequestContract.MEDIA_ID, mediaId)
                    putLong(SubtitleRequestContract.REQUEST_SEQUENCE, sequence)
                })
            }.get(15, TimeUnit.SECONDS)
            assertEquals(subtitleResult.extras.toString(), SessionResult.RESULT_SUCCESS, subtitleResult.resultCode)
            await(controller, "external ASS selected") {
                currentTracks.groups.count { it.type == C.TRACK_TYPE_TEXT } == 3 &&
                    currentTracks.groups.any { it.type == C.TRACK_TYPE_TEXT && it.isSelected &&
                        it.getTrackFormat(0).label?.contains("external.ass") == true }
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
                    currentTracks.groups.count { it.type == C.TRACK_TYPE_TEXT } == 3 &&
                    currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }.getOrNull(1)?.isSelected == true &&
                    currentTracks.groups.none { it.type == C.TRACK_TYPE_TEXT && it.isSelected }
            }
            activity.activityRule.scenario.recreate()
            await(controller, "playback after Activity and Surface recreation") { isPlaying && currentPosition > 800 }
            assertTrue(onMain { controller.playbackParameters.speed == 1.25f })
        } finally {
            onMain { controller.stop(); controller.release() }
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
            "state=${controller.playbackState}, position=${controller.currentPosition}, tracks=${controller.currentTracks.groups}"
        })
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }

    private fun fixture(name: String): Uri = Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/$name")
}
