// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import androidx.lifecycle.ViewModelProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoFrameObservation
import io.github.joyelliot.zivplayer.platform.exoplayer.ExoSurfaceOutput
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.lang.ref.WeakReference
import java.lang.reflect.Proxy
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/** Opt-in real-source regression; never registers or updates the personal media ID. */
class ExoSeekCompletionDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test fun settledPreviewCommitAndFullscreenReturn() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("seekBenchmark") == "true")
        val context = instrumentation.targetContext
        val source = File(context.cacheDir, "seek-benchmark-source.json")
        assumeTrue(source.exists())
        val uri = Uri.parse(JSONObject(source.readText()).getString("source_uri"))
        val app = context.applicationContext as ZivPlayerApplication
        val id = "zivplayer-seek-completion-${UUID.randomUUID()}"
        check(runBlocking { app.mediaRepositories.recent.findById(MediaId(id)) } == null)
        val activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
            .filterIsInstance<MainActivity>().first() }
        val vm = onMain { ViewModelProvider(activity)[PlaybackControllerViewModel::class.java] }
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        val samples = JSONArray()
        val report = JSONObject().put("samples", samples)
        var stage = "prepare"
        fun snapshot() = onMain {
            val owner = (ExoSurfaceOutput::class.java.getDeclaredField("ownerReference")
                .apply { isAccessible = true }.get(null) as? WeakReference<*>)?.get()
            fun field(name: String) = owner?.javaClass?.getDeclaredField(name)?.apply { isAccessible = true }?.get(owner)
            val exo = field("player") as? Player
            fun exoFormat(name: String) = exo?.let {
                Class.forName("androidx.media3.exoplayer.ExoPlayer").getMethod(name).invoke(it)?.toString()
            }
            JSONObject().put("stage", stage).put("timeMs", SystemClock.elapsedRealtime())
                .put("state", controller.playbackState).put("positionMs", controller.currentPosition)
                .put("playing", controller.isPlaying).put("suppression", controller.playbackSuppressionReason)
                .put("error", controller.playerError?.message).put("exoState", exo?.playbackState)
                .put("exoPositionMs", exo?.currentPosition).put("frameMs", ExoFrameObservation.positionMs)
                .put("pendingSeek", field("pendingSeek")?.toString())
                .put("hasFrame", field("pendingSeekHasFrame")).put("syncFrame", field("pendingSeekUsesSyncFrame"))
                .put("lastFrameMs", field("lastFrameMs")).put("lastExact", field("lastCompletedExactPosition"))
                .put("exoLoading", exo?.isLoading).put("exoPlayWhenReady", exo?.playWhenReady)
                .put("exoBufferedMs", exo?.bufferedPosition).put("exoTotalBufferMs", exo?.totalBufferedDuration)
                .put("audioFormat", exoFormat("getAudioFormat")).put("videoFormat", exoFormat("getVideoFormat"))
        }
        fun awaitReady(positionMs: Long? = null, playing: Boolean? = null, suppression: Int? = null) {
            val deadline = SystemClock.elapsedRealtime() + 15_000
            while (true) {
                val sample = snapshot(); samples.put(sample)
                check(!onMain { controller.playerError != null }) { "Seek failed: $sample" }
                if (sample.optInt("exoState") == Player.STATE_READY && !sample.has("pendingSeek") &&
                    (positionMs == null || abs(sample.optLong("exoPositionMs") - positionMs) < if (playing == false) 100 else 2_000) &&
                    (positionMs == null || playing != false || abs(sample.optLong("frameMs") - positionMs) <= 120) &&
                    onMain { controller.playbackState == Player.STATE_READY &&
                        (positionMs == null || abs(controller.currentPosition - positionMs) < 2_000) &&
                        (suppression == null || controller.playbackSuppressionReason == suppression) &&
                        (playing == null || controller.isPlaying == playing) }) return
                check(SystemClock.elapsedRealtime() < deadline) { "Seek stalled: $sample" }
                SystemClock.sleep(100)
            }
        }
        fun fullscreen(value: Boolean) = onMain {
            MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                .apply { isAccessible = true }.invoke(activity, value)
        }
        try {
            onMain {
                controller.setMediaItem(MediaItem.Builder().setMediaId(id).setUri(uri)
                    .setMediaMetadata(MediaMetadata.Builder().setTitle("Seek completion regression")
                        .setExtras(Bundle().apply { putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA,
                            PlaybackRequestSequencer.next()) }).build()).build())
                controller.prepare()
            }
            fullscreen(true)
            awaitReady()
            stage = "ordinary-seek-22193"
            onMain { controller.seekTo(22_193); controller.play() }
            awaitReady(22_193, true)
            for (target in listOf(27_193L, 22_193L, 520_123L, 22_193L)) {
                stage = "preview-$target"
                onMain { vm.previewSeekTo(target) }
                awaitReady(playing = false, suppression = Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING)
                // Real users may hold still before lifting; the commit then has exactly
                // the same target as the completed preview, with no new render needed.
                SystemClock.sleep(750)
                samples.put(snapshot())
                stage = "same-target-commit-$target"
                onMain { vm.seekTo(target) }
                awaitReady(target, true, 0)
                val before = onMain { controller.currentPosition }
                SystemClock.sleep(700)
                check(onMain { controller.isPlaying && controller.currentPosition > before + 400 })
            }
            onMain { controller.pause() }
            for (target in listOf(27_193L, 22_193L, 22_193L)) {
                stage = "paused-seek-$target"
                onMain { controller.seekTo(target) }
                awaitReady(target, false, 0)
                SystemClock.sleep(250)
                check(onMain { !controller.playWhenReady && abs(controller.currentPosition - target) < 100 })
            }
            stage = "paused-preview"
            onMain { vm.previewSeekTo(32_193) }
            awaitReady(playing = false, suppression = Player.PLAYBACK_SUPPRESSION_REASON_SCRUBBING)
            SystemClock.sleep(750)
            stage = "paused-same-target-commit"
            onMain { vm.seekTo(32_193) }
            awaitReady(32_193, false, 0)
            check(onMain { !controller.playWhenReady && controller.playbackSuppressionReason == 0 })
            onMain { controller.play() }
            awaitReady(32_193, true)
            stage = "fullscreen-return"
            fullscreen(false)
            SystemClock.sleep(250)
            fullscreen(true)
            awaitReady(playing = true)
            val before = onMain { controller.currentPosition }
            SystemClock.sleep(700)
            check(onMain { controller.isPlaying && controller.currentPosition > before + 400 })
            check(runBlocking { app.mediaRepositories.recent.findById(MediaId(id)) } == null)
            report.put("functionalChecksPassed", true)
        } finally {
            samples.put(snapshot())
            runCatching {
                val message = onMain {
                    val owner = (ExoSurfaceOutput::class.java.getDeclaredField("ownerReference")
                        .apply { isAccessible = true }.get(null) as? WeakReference<*>)?.get()
                    val exo = owner?.javaClass?.getDeclaredField("player")?.apply { isAccessible = true }
                        ?.get(owner) as? Player
                    val renderers = exo?.javaClass?.getDeclaredField("renderers")?.apply { isAccessible = true }
                        ?.get(exo) as? Array<*>
                    val targetType = Class.forName("androidx.media3.exoplayer.PlayerMessage\$Target")
                    val target = Proxy.newProxyInstance(targetType.classLoader, arrayOf(targetType)) { _, method, _ ->
                        if (method.name == "handleMessage") report.put("renderers", JSONArray(renderers?.map { renderer ->
                            fun read(name: String) = renderer?.javaClass?.getMethod(name)?.apply { isAccessible = true }?.invoke(renderer)
                            fun field(name: String): Any? {
                                var type: Class<*>? = renderer?.javaClass
                                while (type != null) {
                                    val member = runCatching { type!!.getDeclaredField(name) }.getOrNull()
                                    if (member != null) return member.apply { isAccessible = true }.get(renderer)
                                    type = type.superclass
                                }
                                return null
                            }
                            fun primitiveState(value: Any?): JSONObject {
                                val state = JSONObject()
                                var type: Class<*>? = value?.javaClass
                                while (type != null) {
                                    type.declaredFields.filter { it.type.isPrimitive && !java.lang.reflect.Modifier.isStatic(it.modifiers) }
                                        .forEach { state.put(it.name, it.apply { isAccessible = true }.get(value)) }
                                    type = type.superclass
                                }
                                return state
                            }
                            JSONObject().put("name", read("getName")).put("type", read("getTrackType"))
                                .put("state", read("getState")).put("ready", read("isReady"))
                                .put("ended", read("isEnded")).put("readingUs", read("getReadingPositionUs"))
                                .apply {
                                    if (read("getTrackType") == 1) {
                                        put("codec", field("codecInfo")?.toString())
                                        put("decoder", primitiveState(renderer))
                                        put("counters", primitiveState(field("decoderCounters")))
                                        put("sink", primitiveState(field("audioSink")))
                                    }
                                }
                        }))
                        null
                    }
                    exo?.let { Class.forName("androidx.media3.exoplayer.ExoPlayer")
                        .getMethod("createMessage", targetType).invoke(it, target) }
                        ?.let { it.javaClass.getMethod("send").invoke(it) }
                }
                message?.javaClass?.getMethod("blockUntilDelivered", Long::class.javaPrimitiveType)?.invoke(message, 1_000L)
            }.onFailure { report.put("rendererProbeError", it.toString()) }
            try {
                if (report.optBoolean("functionalChecksPassed")) {
                    val renderers = report.getJSONArray("renderers")
                    val audio = (0 until renderers.length()).map(renderers::getJSONObject).first { it.getInt("type") == 1 }
                    check(audio.getBoolean("ready") && audio.getJSONObject("sink").getLong("writtenPcmBytes") > 0) {
                        "Playback resumed without ready PCM audio: $audio"
                    }
                    report.put("passed", true)
                }
            } finally {
                File(context.cacheDir, "exo-seek-completion.json").writeText(report.toString(2))
                onMain { vm.cancelSeekPreview(); controller.stop(); controller.release() }
            }
        }
    }

    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
