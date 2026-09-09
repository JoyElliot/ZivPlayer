// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.ContentResolver
import android.net.Uri
import android.media.MediaExtractor
import android.media.MediaFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.Closeable

internal class OpenMpvMediaSource(val locator: String, val lowLatencyStereo: Boolean = false,
                                  private val release: () -> Unit = {}) : Closeable {
    private var closed = false
    override fun close() { if (!closed) { release(); closed = true } }
}

internal fun interface MpvMediaSourceOpener {
    suspend fun open(locator: String): OpenMpvMediaSource
}

internal class AndroidMpvMediaSourceOpener(private val resolver: ContentResolver) : MpvMediaSourceOpener {
    override suspend fun open(locator: String): OpenMpvMediaSource {
        if (!locator.startsWith("content://", ignoreCase = true)) return OpenMpvMediaSource(locator)
        var opened: OpenMpvMediaSource? = null
        var descriptor: android.os.ParcelFileDescriptor? = null
        try {
            withContext(Dispatchers.IO) {
                val owned = resolver.openFileDescriptor(Uri.parse(locator), "r")
                    ?: error("The selected media is no longer readable.")
                descriptor = owned
                // libmpv borrows this FD. It must never close it or reopen a /proc alias.
                // OpenSL ES avoids AudioTrack's large first-write chunk on confirmed stereo
                // local files. Unknown or multichannel sources retain AudioTrack's channel support.
                val stereo = isKnownStereo(owned.fileDescriptor)
                opened = OpenMpvMediaSource("fd://${owned.fd}", lowLatencyStereo = stereo, release = owned::close)
            }
            return checkNotNull(opened)
        } catch (failure: Throwable) {
            runCatching { if (opened != null) opened.close() else descriptor?.close() }
                .exceptionOrNull()?.let(failure::addSuppressed)
            throw failure
        }
    }

    private fun isKnownStereo(descriptor: java.io.FileDescriptor): Boolean {
        // Do not probe a pipe or consume a non-seekable provider stream before libmpv.
        val originalOffset = try {
            if (!android.system.OsConstants.S_ISREG(android.system.Os.fstat(descriptor).st_mode)) return false
            android.system.Os.lseek(descriptor, 0, android.system.OsConstants.SEEK_CUR)
        } catch (_: Exception) { return false }
        val extractor = MediaExtractor()
        return try {
            extractor.setDataSource(descriptor)
            val audio = (0 until extractor.trackCount).map(extractor::getTrackFormat)
                .filter { it.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true }
            audio.isNotEmpty() && audio.all {
                it.containsKey(MediaFormat.KEY_CHANNEL_COUNT) && it.getInteger(MediaFormat.KEY_CHANNEL_COUNT) in 1..2 &&
                    it.containsKey(MediaFormat.KEY_SAMPLE_RATE) && it.getInteger(MediaFormat.KEY_SAMPLE_RATE) in 8_000..192_000
            }
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled
        } catch (_: Exception) { false } finally {
            try { extractor.release() } finally {
                android.system.Os.lseek(descriptor, originalOffset, android.system.OsConstants.SEEK_SET)
            }
        }
    }
}
