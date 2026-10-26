// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.libvlc

import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.videolan.libvlc.LibVLC
import org.videolan.libvlc.Media
import org.videolan.libvlc.interfaces.IMedia

/** Probe only local sources; an unknown transform uses the supported codec-copy path. */
internal suspend fun hasUntransformedLocalVideo(context: Context, library: LibVLC, uri: Uri): Boolean {
    if (uri.scheme !in setOf(null, "file", "content")) return false
    var descriptor: ParcelFileDescriptor? = null
    var probe: Media? = null
    return try {
        // Preparse consumes an FD's shared file offset. Give it a separately
        // opened descriptor and Media so playback's source remains untouched.
        if (uri.scheme == "content") withContext(Dispatchers.IO) {
            descriptor = context.contentResolver.openFileDescriptor(uri, "r")
        }
        val media = if (uri.scheme == "content") Media(library, checkNotNull(descriptor).fileDescriptor) else Media(library, uri)
        probe = media
        probeUntransformedVideo(media)
    } catch (failure: CancellationException) { throw failure
    } catch (_: Exception) { false
    } finally {
        probe?.setEventListener(null)
        withContext(NonCancellable + Dispatchers.IO) {
            try { probe?.release() } finally { descriptor?.close() }
        }
    }
}

private suspend fun probeUntransformedVideo(media: Media): Boolean {
    val parsed = CompletableDeferred<Boolean>()
    media.setEventListener { event ->
        // The pinned eap29 Java enum still has VLC 3 values. Its JNI forwards
        // the VLC 4 status unchanged: 1=pending, 6=done (4 is timeout).
        if (event.type == IMedia.Event.ParsedChanged && event.parsedStatus >= 2) {
            val videos = media.getTracks(IMedia.Track.Type.Video)?.filterIsInstance<IMedia.VideoTrack>()
            parsed.complete(event.parsedStatus == 6 && !videos.isNullOrEmpty() &&
                videos.all { it.orientation == 0 })
        }
    }
    return try {
        if (!media.parseAsync(IMedia.Parse.ParseLocal or IMedia.Parse.ParseForced, 1_000)) false
        else withTimeoutOrNull(1_500) { parsed.await() } ?: false
    } finally { media.setEventListener(null) }
}
