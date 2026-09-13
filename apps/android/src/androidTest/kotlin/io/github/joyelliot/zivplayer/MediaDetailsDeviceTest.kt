// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import androidx.test.platform.app.InstrumentationRegistry
import io.github.joyelliot.zivplayer.core.media.*
import io.github.joyelliot.zivplayer.data.media.LocalMediaDetailsStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.UUID

/** Reads real tags and pixels through a content provider without registering personal history. */
class MediaDetailsDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val app get() = context.applicationContext as ZivPlayerApplication
    private fun request(name: String) = MediaDetailsRequest(DurableMediaUri(
        "content://io.github.joyelliot.zivplayer.test.fixtures/$name?metadata-test=${UUID.randomUUID()}"))

    @Test fun tagsEmbeddedCoverAndVideoFrameHaveRealContentWithoutChangingHistory() = runBlocking {
        val before = app.recentMediaRepository.observeRecentlyOpened(1000).first()
        val music = checkNotNull(app.mediaDetailsRepository.load(request("metadata-music.mp3")))
        assertEquals("Evening Test", music.title)
        assertEquals("ZivPlayer Artist", music.artist)
        assertEquals("Original Test Album", music.album)
        assertEquals("ZivPlayer Ensemble", music.albumArtist)
        assertEquals("2026", music.year)
        assertEquals("3/8", music.trackNumber)
        assertEquals(48000, music.sampleRate)
        assertEquals(2, music.channelCount)
        assertTrue(checkNotNull(music.durationMs) in 7900..8300)
        assertTrue(checkNotNull(music.sizeBytes) > 100_000)
        assertNotNull(music.audioCodec)
        image(music).let { bitmap ->
            try {
                assertTrue(bitmap.width <= 768 && bitmap.height <= 768)
                val gold = bitmap.getPixel(bitmap.width / 4, bitmap.height / 2)
                assertTrue("Embedded gold block was not extracted", Color.red(gold) > 190 && Color.green(gold) > 120 && Color.blue(gold) < 100)
            } finally { bitmap.recycle() }
        }
        val video = checkNotNull(app.mediaDetailsRepository.load(request("metadata-video.mp4")))
        assertEquals("Original Video Sample", video.title)
        assertEquals("ZivPlayer Studio", video.artist)
        assertEquals(640, video.videoWidth)
        assertEquals(360, video.videoHeight)
        assertEquals("video/avc", video.videoCodec)
        assertEquals("audio/mp4a-latm", video.audioCodec)
        image(video).let { bitmap ->
            try {
                assertTrue(bitmap.width > bitmap.height)
                val colors = (1..8).map { bitmap.getPixel(bitmap.width * it / 10, bitmap.height / 2) }.toSet()
                assertTrue("Video fallback thumbnail is empty", colors.size >= 4)
            } finally { bitmap.recycle() }
        }
        assertEquals(before, app.recentMediaRepository.observeRecentlyOpened(1000).first())
    }

    @Test fun cacheInvalidatesChangedHintsAndRepairsTruncatedArtwork() = runBlocking {
        val source = request("metadata-music.mp3")
        val store = app.mediaDetailsRepository
        val first = checkNotNull(store.load(source.copy(fileName = "Before.mp3", mimeType = "audio/mpeg")))
        val renamed = checkNotNull(store.load(source.copy(fileName = "After.mp3", mimeType = "audio/x-mp3")))
        assertEquals("After.mp3", renamed.fileName)
        assertEquals("audio/x-mp3", renamed.mimeType)
        assertNotEquals(first.artworkLocator, renamed.artworkLocator)
        val art = File(context.cacheDir, "media-details/${Uri.parse(checkNotNull(renamed.artworkLocator)).lastPathSegment}")
        art.writeBytes(byteArrayOf(0xff.toByte(), 0xd8.toByte(), 0))
        val repaired = checkNotNull(store.load(source.copy(fileName = "After.mp3", mimeType = "audio/x-mp3")))
        image(repaired).recycle()
        // A new repository exercises the AtomicFile read path, including recovery of a backed-up file.
        val key = art.name.removeSuffix(".jpg")
        val json = File(art.parentFile, "$key.json")
        json.copyTo(File(json.path + ".bak"), overwrite = true)
        json.writeText("broken")
        val reloaded = checkNotNull(LocalMediaDetailsStore(context).load(source.copy(fileName = "After.mp3", mimeType = "audio/x-mp3")))
        assertEquals("Evening Test", reloaded.title)
        assertFalse(File(json.path + ".bak").exists())
        image(reloaded).recycle()
    }

    private fun image(details: MediaDetails) = context.contentResolver.openInputStream(Uri.parse(checkNotNull(details.artworkLocator)))
        ?.use { checkNotNull(BitmapFactory.decodeStream(it)) } ?: error("Artwork stream is unavailable")
}
