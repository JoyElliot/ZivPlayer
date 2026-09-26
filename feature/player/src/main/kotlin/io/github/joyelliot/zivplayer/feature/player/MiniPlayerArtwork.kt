// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import android.content.Context
import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.ZivLibraryTile
import io.github.joyelliot.zivplayer.designsystem.ZivPlaybackIcon
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

@Composable
internal fun MiniPlayerArtwork(state: PlayerUiState) {
    val context = LocalContext.current.applicationContext
    // A new media identity starts with an empty image, never a frame from the previous item.
    // Playback position is deliberately not a key: the 250 ms ticker must not decode frames.
    key(state.mediaId, state.sourceUri, state.playbackIdentity, state.hasVideo) {
        val frame by produceState<Bitmap?>(null) {
            if (state.hasVideo && state.sourceUri != null) {
                value = MiniPlayerFrames.load(context, state.sourceUri, state.playbackIdentity ?: state.mediaId.orEmpty())
            }
        }
        val bitmap = frame
        if (bitmap != null) {
            Image(bitmap.asImageBitmap(), contentDescription = null,
                modifier = Modifier.size(width = 48.dp, height = 34.dp).clip(RoundedCornerShape(10.dp))
                    .testTag("mini_video_thumbnail"),
                contentScale = ContentScale.Crop)
        } else {
            ZivLibraryTile(if (state.hasVideo) ZivPlaybackIcon.PLAY else ZivPlaybackIcon.AUDIO,
                Modifier.size(width = 48.dp, height = 34.dp).testTag("mini_artwork_placeholder"))
        }
    }
}

private object MiniPlayerFrames {
    // In-memory only: retain a few small previews across navigation without persisting URI grants.
    private val cache = object : LruCache<String, Bitmap>(4 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.allocationByteCount
    }
    private val decoder = Semaphore(1)

    suspend fun load(context: Context, source: String, identity: String): Bitmap? = withContext(Dispatchers.IO) {
        val uri = Uri.parse(source)
        if (uri.scheme !in setOf("content", "file", "android.resource")) return@withContext null
        val cacheKey = "$identity\n$source"
        cache.get(cacheKey)?.let { return@withContext it }
        decoder.withPermit {
            currentCoroutineContext().ensureActive()
            cache.get(cacheKey)?.let { return@withPermit it }
            val bitmap = try {
                MediaMetadataRetriever().use { retriever ->
                    retriever.setDataSource(context, uri)
                    retriever.getScaledFrameAtTime(-1L, MediaMetadataRetriever.OPTION_CLOSEST_SYNC, 320, 180)
                }
            } catch (_: Exception) {
                // A revoked grant, unsupported codec or unavailable provider leaves the icon visible.
                null
            }
            currentCoroutineContext().ensureActive()
            bitmap?.also { cache.put(cacheKey, it) }
        }
    }
}
