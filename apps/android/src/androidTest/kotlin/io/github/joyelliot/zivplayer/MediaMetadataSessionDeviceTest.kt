// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.ComponentName
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestMetadata
import io.github.joyelliot.zivplayer.platform.playback.PlaybackRequestSequencer
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Exercises real service -> MediaController metadata events and occurrence-token preservation. */
class MediaMetadataSessionDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    @Test fun metadataReachesControllerAndSwitchingMediaKeepsItsIdentity() {
        val app = context.applicationContext as ZivPlayerApplication
        val id = "metadata-session-${UUID.randomUUID()}"
        val music = "content://io.github.joyelliot.zivplayer.test.fixtures/metadata-music.mp3?session=$id"
        val video = "content://io.github.joyelliot.zivplayer.test.fixtures/metadata-video.mp4?session=$id"
        val controller = onMain { MediaController.Builder(context, SessionToken(context,
            ComponentName(context, PlaybackService::class.java))).buildAsync() }.get(15, TimeUnit.SECONDS)
        val activity = onMain { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
            .filterIsInstance<MainActivity>().first() }
        fun setSource(uri: String, mediaId: String): Long {
            val sequence = PlaybackRequestSequencer.next()
            onMain { controller.setMediaItem(MediaItem.Builder().setMediaId(mediaId).setUri(uri)
                .setMediaMetadata(MediaMetadata.Builder().setTitle("Metadata pending").setExtras(Bundle().apply {
                    putString(PlaybackRequestMetadata.TOKEN_EXTRA, id)
                    putLong(PlaybackRequestMetadata.SEQUENCE_EXTRA, sequence)
                }).build()).build()); controller.prepare() }
            return sequence
        }
        try {
            val first = setSource(music, id)
            await { onMain { controller.mediaMetadata.title?.toString() == "Evening Test" && controller.mediaMetadata.artworkUri != null } }
            onMain {
                assertEquals("ZivPlayer Artist", controller.mediaMetadata.artist.toString())
                assertEquals("Original Test Album", controller.mediaMetadata.albumTitle.toString())
                assertEquals(id, controller.currentMediaItem?.mediaId)
                assertEquals(id, controller.mediaMetadata.extras?.getString(PlaybackRequestMetadata.TOKEN_EXTRA))
                assertEquals(first, controller.mediaMetadata.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA))
                assertEquals(music, controller.mediaMetadata.extras?.getString(PlaybackRequestMetadata.SOURCE_URI_EXTRA))
                assertFalse(controller.playWhenReady)
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, true)
            }
            await { onMain { activity.hasWindowFocus() && activity.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE } }
            SystemClock.sleep(800)
            val screenshot = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
            try { File(context.cacheDir, "metadata-session-landscape.png").outputStream().use { screenshot.compress(Bitmap.CompressFormat.PNG, 100, it) } }
            finally { screenshot.recycle() }
            setSource(music + "-racing", "$id-racing")
            val second = setSource(video, "$id-video")
            await { onMain { controller.currentMediaItem?.mediaId == "$id-video" &&
                controller.mediaMetadata.title?.toString() == "Original Video Sample" && controller.playbackState == Player.STATE_READY } }
            SystemClock.sleep(300)
            onMain {
                assertEquals("ZivPlayer Studio", controller.mediaMetadata.artist.toString())
                assertEquals(second, controller.mediaMetadata.extras?.getLong(PlaybackRequestMetadata.SEQUENCE_EXTRA))
                assertEquals(video, controller.mediaMetadata.extras?.getString(PlaybackRequestMetadata.SOURCE_URI_EXTRA))
                assertNull(controller.playerError)
            }
            runBlocking {
                for (key in listOf(id, "$id-racing", "$id-video")) assertNull(app.recentMediaRepository.findById(MediaId(key)))
            }
        } finally {
            onMain {
                controller.stop(); controller.clearMediaItems(); controller.release()
                MainActivity::class.java.getDeclaredMethod("changeFullscreen", Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }.invoke(activity, false)
            }
        }
    }

    private fun await(condition: () -> Boolean) {
        val end = SystemClock.elapsedRealtime() + 12_000
        while (SystemClock.elapsedRealtime() < end) { if (condition()) return; SystemClock.sleep(20) }
        error("Media metadata did not reach the controller")
    }
    private fun <T> onMain(action: () -> T): T {
        var result: Result<T>? = null
        instrumentation.runOnMainSync { result = runCatching(action) }
        return checkNotNull(result).getOrThrow()
    }
}
