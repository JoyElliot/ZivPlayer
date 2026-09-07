// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionToken
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import io.github.joyelliot.zivplayer.platform.playback.TemporarySpeedContract
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.TimeUnit

/** Real Media3/service/native ownership coverage; touch recognition is tested separately. */
class PlaybackInteractionTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val activity = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test fun reservedUserOpenWinsOverAnEndingQueueItem() = withController { controller ->
        installQueue(controller)
        onMain { controller.repeatMode = Player.REPEAT_MODE_OFF; controller.play() }
        await(controller, "playing before user selection") { isPlaying && currentPosition > 300L }
        // SAF registration reserves this sequence before it eventually supplies Media3 items.
        val reserved = PlaybackRequestSequencer.next()
        onMain { controller.seekTo(durationNearEnd(controller)) }
        await(controller, "old item ends without replacing pending user selection") {
            playbackState == Player.STATE_ENDED && currentMediaItemIndex == 0
        }
        assertEquals(reserved, PlaybackRequestSequencer.current())
        onMain {
            controller.setMediaItem(controller.getMediaItemAt(1).buildUpon().setMediaId("reserved-replacement")
                .setMediaMetadata(MediaMetadata.Builder().setTitle("Reserved replacement").setExtras(Bundle().apply {
                    putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, reserved)
                    putString(PlaybackRequestMetadata.TOKEN_EXTRA, "reserved-$reserved")
                }).build()).build())
        }
        await(controller, "reserved user item is accepted") {
            currentMediaItem?.mediaId == "reserved-replacement" && playbackState == Player.STATE_READY && !playWhenReady
        }
    }

    @Test fun queueAdvancesRepeatsAndPausedSkipStaysPaused() = withController { controller ->
        installQueue(controller)
        onMain { controller.repeatMode = Player.REPEAT_MODE_OFF; controller.play() }
        await(controller, "first playing") { isPlaying && currentPosition > 300L && currentMediaItemIndex == 0 }
        onMain { controller.seekTo(durationNearEnd(controller)) }
        await(controller, "automatic next item") { isPlaying && currentPosition > 300L && currentMediaItemIndex == 1 }
        assertEquals("queue-second", onMain { controller.currentMediaItem?.mediaId })
        onMain { controller.repeatMode = Player.REPEAT_MODE_ALL; controller.seekTo(durationNearEnd(controller)) }
        await(controller, "repeat all wraps to first") { isPlaying && currentPosition > 300L && currentMediaItemIndex == 0 }
        onMain { controller.pause() }
        await(controller, "pause") { !playWhenReady }
        onMain { controller.seekToNextMediaItem() }
        await(controller, "paused next") { currentMediaItemIndex == 1 && playbackState == Player.STATE_READY && !playWhenReady }
        SystemClock.sleep(500)
        assertTrue(onMain { !controller.isPlaying && !controller.playWhenReady })
        onMain { controller.repeatMode = Player.REPEAT_MODE_OFF; controller.play(); controller.seekTo(durationNearEnd(controller)) }
        await(controller, "last item stops with repeat off") { playbackState == Player.STATE_ENDED && !playWhenReady && currentMediaItemIndex == 1 }
    }

    @Test fun temporarySpeedRestoresOnReleasePauseExplicitChoiceSwitchAndDisconnect() = withController { controller ->
        installQueue(controller)
        onMain { controller.repeatMode = Player.REPEAT_MODE_ONE; controller.play() }
        await(controller, "playing") { isPlaying && currentPosition > 300L }
        onMain { controller.setPlaybackSpeed(1.25f) }
        await(controller, "base speed") { playbackParameters.speed == 1.25f }
        temporary(controller, 1, 2f)
        await(controller, "hold speed") { playbackParameters.speed == 2f }
        temporary(controller, 1, 0.5f)
        await(controller, "hold slow motion") { playbackParameters.speed == 0.5f }
        temporary(controller, 1, null)
        await(controller, "release restores original") { playbackParameters.speed == 1.25f }
        temporary(controller, 1, 4f)
        assertEquals(1.25f, onMain { controller.playbackParameters.speed }, 0f)

        temporary(controller, 2, 3f)
        temporary(controller, 1, null)
        await(controller, "stale release cannot end new hold") { playbackParameters.speed == 3f }
        onMain { controller.pause() }
        await(controller, "pause restores hold") { !playWhenReady && playbackParameters.speed == 1.25f }
        onMain { controller.play() }
        await(controller, "resumed") { isPlaying }
        temporary(controller, 3, 4f)
        onMain { controller.setPlaybackSpeed(1.5f) }
        await(controller, "explicit speed wins") { playbackParameters.speed == 1.5f }
        temporary(controller, 3, null)
        temporary(controller, 3, 2f)
        assertEquals(1.5f, onMain { controller.playbackParameters.speed }, 0f)

        temporary(controller, 4, 3f)
        onMain { controller.seekToNextMediaItem() }
        await(controller, "next item has default speed") { currentMediaItemIndex == 1 && isPlaying && playbackParameters.speed != 3f }
        val base = onMain { controller.playbackParameters.speed }
        val gestureController = connect()
        try {
            temporary(gestureController, 5, 2f)
            await(controller, "second controller owns hold") { playbackParameters.speed == 2f }
        } finally { onMain { gestureController.release() } }
        await(controller, "controller disconnect restores speed") { playbackParameters.speed == base }
    }

    @Test fun editingQueuePreservesCurrentPlaybackAndClearReleasesIt() = withController { controller ->
        installQueue(controller)
        onMain { controller.repeatMode = Player.REPEAT_MODE_ONE; controller.play() }
        await(controller, "playing before editing") { isPlaying && currentPosition > 300L }
        onMain { controller.setPlaybackSpeed(1.5f) }
        await(controller, "editing base speed") { playbackParameters.speed == 1.5f }
        val firstUid = onMain { controller.currentTimeline.getUidOfPeriod(controller.currentPeriodIndex) }
        val firstPosition = onMain { controller.currentPosition }
        val added = onMain { controller.getMediaItemAt(1).buildUpon().setMediaId("queue-added").build() }
        onMain {
            controller.addMediaItem(0, added)
            controller.moveMediaItem(1, 2)
        }
        await(controller, "back-to-back add and move") {
            mediaItemCount == 3 && currentMediaItemIndex == 2 && currentMediaItem?.mediaId == "queue-first" &&
                (0 until mediaItemCount).map { getMediaItemAt(it).mediaId } == listOf("queue-added", "queue-second", "queue-first")
        }
        SystemClock.sleep(300)
        assertEquals(firstUid, onMain { controller.currentTimeline.getUidOfPeriod(controller.currentPeriodIndex) })
        assertTrue(onMain { controller.isPlaying && controller.currentPosition >= firstPosition && controller.playbackParameters.speed == 1.5f })
        onMain { controller.replaceMediaItem(0, added.buildUpon().setMediaId("queue-replaced").build()) }
        await(controller, "replace surrounding item") { getMediaItemAt(0).mediaId == "queue-replaced" }
        onMain { controller.removeMediaItem(1) }
        await(controller, "remove surrounding item") { mediaItemCount == 2 && currentMediaItemIndex == 1 }
        assertEquals(firstUid, onMain { controller.currentTimeline.getUidOfPeriod(controller.currentPeriodIndex) })
        onMain { controller.removeMediaItem(1) }
        await(controller, "remove current chooses surviving item") { mediaItemCount == 1 && currentMediaItem?.mediaId == "queue-replaced" && isPlaying && currentPosition > 300L }
        onMain { controller.clearMediaItems() }
        await(controller, "clear current queue") { mediaItemCount == 0 && playbackState == Player.STATE_IDLE && !playWhenReady }
        onMain { controller.setMediaItems(emptyList()); controller.addMediaItem(added) }
        await(controller, "add to an empty queue") { mediaItemCount == 1 && playbackState == Player.STATE_READY && !playWhenReady }
        onMain { controller.addMediaItem(added.buildUpon().setMediaId("queue-after-pause").build()); controller.play() }
        await(controller, "play follows pending addition") { mediaItemCount == 2 && isPlaying && currentPosition > 300L }
        onMain { controller.removeMediaItem(0); controller.pause() }
        await(controller, "pause does not cancel current removal or restart its successor") {
            mediaItemCount == 1 && currentMediaItem?.mediaId == "queue-after-pause" && playbackState == Player.STATE_READY && !playWhenReady
        }
        SystemClock.sleep(300)
        assertTrue(onMain { !controller.isPlaying && !controller.playWhenReady })
        onMain { controller.addMediaItem(0, added); controller.moveMediaItem(0, 1); controller.stop() }
        await(controller, "stop preserves admitted surrounding edits") {
            mediaItemCount == 2 && getMediaItemAt(0).mediaId == "queue-after-pause" && getMediaItemAt(1).mediaId == "queue-added" &&
                playbackState == Player.STATE_IDLE && !playWhenReady
        }
        installQueue(controller)
        onMain { controller.play() }
        await(controller, "playing before replacement and pause") { isPlaying && currentPosition > 300L }
        val replacements = onMain { (0..1).map { index ->
            controller.getMediaItemAt(index).buildUpon().setMediaId("replacement-$index")
                .setMediaMetadata(MediaMetadata.Builder().setTitle("Replacement $index").build()).build()
        } }
        onMain { controller.setMediaItems(replacements); controller.pause(); controller.addMediaItem(2, added) }
        await(controller, "replacement followed by pause and append retains the replacement") {
            mediaItemCount == 3 && (0 until mediaItemCount).map { getMediaItemAt(it).mediaId } ==
                listOf("replacement-0", "replacement-1", "queue-added") && playbackState == Player.STATE_READY && !playWhenReady
        }
        SystemClock.sleep(300)
        assertTrue(onMain { !controller.isPlaying && controller.getMediaItemAt(0).mediaId == "replacement-0" })
    }

    private fun installQueue(controller: MediaController) {
        val sequence = PlaybackRequestSequencer.next()
        val items = listOf("baseline-av.mp4", "tracks-subtitles.mkv").mapIndexed { index, file ->
            MediaItem.Builder().setMediaId(if (index == 0) "queue-first" else "queue-second")
                .setUri(Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/$file"))
                .setMediaMetadata(MediaMetadata.Builder().setTitle("Queue test ${index + 1}")
                    .setExtras(Bundle().apply {
                        putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                        putString(PlaybackRequestMetadata.TOKEN_EXTRA, "queue-test-$sequence")
                    }).build()).build()
        }
        onMain { controller.setMediaItems(items, 0, 0L); controller.prepare() }
        await(controller, "two-item queue ready") { mediaItemCount == 2 && currentMediaItemIndex == 0 && playbackState == Player.STATE_READY }
    }

    private fun temporary(controller: MediaController, token: Long, rate: Float?) {
        val result = onMain { controller.sendCustomCommand(SessionCommand(TemporarySpeedContract.ACTION, Bundle.EMPTY), Bundle().apply {
            putLong(TemporarySpeedContract.TOKEN, token)
            putString(TemporarySpeedContract.MEDIA_ID, controller.currentMediaItem?.mediaId)
            putLong(TemporarySpeedContract.SEQUENCE, controller.currentMediaItem?.mediaMetadata?.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA) ?: 0L)
            putInt(TemporarySpeedContract.INDEX, controller.currentMediaItemIndex)
            if (rate != null) putFloat(TemporarySpeedContract.RATE, rate)
        }) }.get(15, TimeUnit.SECONDS)
        assertEquals(result.extras.toString(), SessionResult.RESULT_SUCCESS, result.resultCode)
    }

    private fun durationNearEnd(controller: MediaController): Long {
        check(controller.duration > 1_000L)
        return controller.duration - 350L
    }

    private fun connect(): MediaController = onMain {
        val context = instrumentation.targetContext
        MediaController.Builder(context, SessionToken(context, ComponentName(context, PlaybackService::class.java))).buildAsync()
    }.get(15, TimeUnit.SECONDS)

    private fun withController(block: (MediaController) -> Unit) {
        onMain { activity.activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
        val controller = connect()
        val repeat = onMain { controller.repeatMode }
        val trace = StringBuilder()
        onMain { controller.addListener(object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) {
                trace.appendLine("${SystemClock.elapsedRealtime()} events=${(0 until events.size()).map(events::get)} global=${PlaybackRequestSequencer.current()} " +
                    "itemSeq=${player.currentMediaItem?.mediaMetadata?.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA)} index=${player.currentMediaItemIndex} " +
                    "state=${player.playbackState} playing=${player.playWhenReady} speed=${player.playbackParameters.speed} " +
                    "commands=${(0 until player.availableCommands.size()).map(player.availableCommands::get)} error=${player.playerError}")
            }
        }) }
        try { block(controller) } finally {
            onMain { controller.repeatMode = repeat; controller.stop(); controller.release() }
            java.io.File(instrumentation.targetContext.cacheDir, "product-service-trace-${SystemClock.elapsedRealtime()}.txt").writeText(trace.toString())
        }
    }

    private fun await(controller: MediaController, description: String, predicate: MediaController.() -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        while (SystemClock.elapsedRealtime() < deadline) {
            if (onMain { check(controller.playerError == null) { "$description: ${controller.playerError}" }; controller.predicate() }) return
            SystemClock.sleep(50)
        }
        error("Timed out: $description; " + onMain { "state=${controller.playbackState}, index=${controller.currentMediaItemIndex}, playing=${controller.playWhenReady}, speed=${controller.playbackParameters.speed}" })
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
