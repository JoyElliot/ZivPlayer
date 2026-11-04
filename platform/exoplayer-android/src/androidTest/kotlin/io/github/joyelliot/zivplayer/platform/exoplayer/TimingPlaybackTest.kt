// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import android.net.Uri
import android.os.SystemClock
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** 使用真实 extractor、MediaCodec 和 AudioTrack；物理扬声器与屏幕的偏差仍需外部测量。 */
class TimingPlaybackTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext

    @Test fun audioOffsetsKeepPlayingAcrossPauseSeekAndSpeedChanges() {
        val media = MediaItem.fromUri(fixture("continuous-av.mp4"))
        for (offset in listOf(-10_000, -50, 500)) withPlayer(media, PlaybackOptions(audioDelayMs = offset, cacheSizeMiB = 16)) { player ->
            await(player, "ready with audio offset $offset") { playbackState == Player.STATE_READY }
            onMain { player.play() }
            await(player, "clock with audio offset $offset") { isPlaying && currentPosition >= 200 }
            onMain { player.pause() }
            await(player, "pause") { !isPlaying }
            val paused = onMain { player.currentPosition }
            SystemClock.sleep(120)
            assertTrue("Paused clock moved", kotlin.math.abs(onMain { player.currentPosition } - paused) < 100)
            onMain { player.seekTo(1000) }
            await(player, "paused seek") { playbackState == Player.STATE_READY && currentPosition in 990..1100 }
            onMain { player.playbackParameters = PlaybackParameters(2f); player.play() }
            await(player, "double speed") { isPlaying && currentPosition >= 1400 }
            onMain { player.playbackParameters = PlaybackParameters(1f) }
            await(player, "normal speed") { isPlaying && currentPosition >= 1700 }
            assertNotNull(onMain { player.audioFormat })
            assertNotNull(onMain { player.videoFormat })
        }
    }

    @Test fun embeddedAndExternalSubtitlesFollowSignedOffsetsDuringPausedSeeking() {
        val observations = mutableListOf<String>()
        val failures = mutableListOf<String>()
        try {
            for (external in listOf(false, true)) for (offset in listOf(0, -1000, 1000)) {
                val builder = MediaItem.Builder().setUri(fixture(if (external) "baseline-av.mp4" else "tracks-subtitles.mkv"))
                if (external) builder.setSubtitleConfigurations(listOf(MediaItem.SubtitleConfiguration.Builder(fixture("external.srt"))
                    .setMimeType("application/x-subrip").setLanguage("en").setSelectionFlags(C.SELECTION_FLAG_DEFAULT).build()))
                withPlayer(builder.build(), PlaybackOptions(subtitleDelayMs = offset)) { player ->
                    onMain { player.addListener(object : Player.Listener {
                        override fun onCues(cueGroup: androidx.media3.common.text.CueGroup) {
                            observations += "event offset=$offset external=$external position=${player.currentPosition} cues=${cueGroup.cues.map { it.text }}"
                        }
                    }) }
                    await(player, "subtitle source ready") { playbackState == Player.STATE_READY }
                    for ((sourceTime, text) in listOf(1800L to "SRT subtitle", 5500L to "中文 SRT", 9500L to "Subtitle end")) {
                        val target = sourceTime + offset
                        onMain { player.seekTo(target) }
                        runCatching { await(player, "offset=$offset external=$external cue=$text") {
                            playbackState == Player.STATE_READY && currentPosition in (target - 20)..(target + 20) &&
                                currentCues.cues.any { it.text?.contains(text) == true }
                        } }.exceptionOrNull()?.let { failures += it.message.orEmpty() }
                        onMain { observations += "seek offset=$offset external=$external target=$target duration=${player.duration} position=${player.currentPosition} cues=${player.currentCues.cues.map { it.text }}" }
                        assertFalse(onMain { player.isPlaying })
                    }
                }
            }
        } finally {
            File(context.cacheDir, "subtitle-timing-observations.txt").writeText(observations.joinToString("\n"))
        }
        assertTrue(failures.joinToString("\n"), failures.isEmpty())
    }

    @Test fun sixtySecondSubtitleDelayDoesNotExtendTheVideoEnd() {
        val media = MediaItem.Builder().setUri(fixture("baseline-av.mp4"))
            .setSubtitleConfigurations(listOf(MediaItem.SubtitleConfiguration.Builder(fixture("external.srt"))
                .setMimeType("application/x-subrip").setLanguage("en").setSelectionFlags(C.SELECTION_FLAG_DEFAULT).build())).build()
        withPlayer(media, PlaybackOptions(subtitleDelayMs = 60_000)) { player ->
            await(player, "delayed subtitle source ready") { playbackState == Player.STATE_READY }
            onMain { player.seekTo(10_000); player.play() }
            await(player, "AV ends without waiting another minute", 8000) { playbackState == Player.STATE_ENDED }
            assertTrue(onMain { player.currentPosition <= player.duration + 100 })
        }
    }

    @Test fun disabledAndUnselectedExternalSubtitlesDoNotOpenMissingFiles() {
        val media = MediaItem.Builder().setUri(fixture("baseline-av.mp4"))
            .setSubtitleConfigurations(listOf(
                MediaItem.SubtitleConfiguration.Builder(fixture("external.srt"))
                    .setId("valid").setMimeType("application/x-subrip").setLanguage("en")
                    .setSelectionFlags(C.SELECTION_FLAG_DEFAULT).build(),
                MediaItem.SubtitleConfiguration.Builder(Uri.fromFile(File(context.cacheDir, "missing-${System.nanoTime()}.srt")))
                    .setId("missing").setMimeType("application/x-subrip").setLanguage("ja").build(),
            )).build()
        for (disabled in listOf(false, true)) for (offset in listOf(0, -1000, 1000)) {
            withPlayer(media, PlaybackOptions(subtitleDelayMs = offset), disableText = disabled) { player ->
                await(player, "unselected missing subtitle, disabled=$disabled offset=$offset") { playbackState == Player.STATE_READY }
                onMain { player.seekTo(9500L + offset) }
                if (!disabled) await(player, "initial seek inside last cue") {
                    currentCues.cues.any { it.text?.contains("Subtitle end") == true }
                }
                onMain { player.play() }
                await(player, "AV still plays with missing unselected subtitle") { isPlaying }
            }
        }
    }

    private fun fixture(name: String): Uri {
        val file = File(context.cacheDir, "timing-$name")
        instrumentation.context.assets.open(name).use { input -> file.outputStream().use(input::copyTo) }
        return Uri.fromFile(file)
    }

    private fun withPlayer(media: MediaItem, options: PlaybackOptions, disableText: Boolean = false, block: (ExoPlayer) -> Unit) {
        val player = onMain {
            ExoPlayer.Builder(context, DelayRenderersFactory(context, options.audioDelayMs))
                .setLoadControl(playbackLoadControlBuilder(options).build()).build().apply {
                    volume = 0f
                    trackSelectionParameters = trackSelectionParameters.buildUpon().setPreferredTextLanguage("en")
                        .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, disableText).build()
                    setMediaSource(playbackMediaSource(context, media, options.subtitleDelayMs))
                    prepare()
                }
        }
        try { block(player) } finally { onMain { player.release() } }
    }

    private fun await(player: ExoPlayer, reason: String, timeoutMs: Long = 15_000, condition: ExoPlayer.() -> Boolean) {
        val end = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < end) {
            if (onMain { check(player.playerError == null) { "$reason: ${player.playerError?.stackTraceToString()}" }; player.condition() }) return
            SystemClock.sleep(30)
        }
        error(onMain { "$reason: state=${player.playbackState}, position=${player.currentPosition}, cues=${player.currentCues.cues}" })
    }

    private fun <T> onMain(block: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(block) }
        return checkNotNull(result).getOrThrow()
    }
}
