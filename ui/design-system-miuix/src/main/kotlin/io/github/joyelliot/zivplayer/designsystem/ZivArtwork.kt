// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.designsystem

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import top.yukonga.miuix.kmp.theme.MiuixTheme

private val artworkImages = object : LruCache<String, Bitmap>(12 * 1024 * 1024) {
    override fun sizeOf(key: String, value: Bitmap): Int = value.allocationByteCount
}
private data class LoadedArtwork(val locator: String, val bitmap: Bitmap)

/** Local artwork only; bitmap decoding is bounded and never runs on the UI thread. */
@Composable
fun ZivArtwork(artworkLocator: String?, modifier: Modifier = Modifier,
    icon: ZivPlaybackIcon = ZivPlaybackIcon.AUDIO,
    color: Color = MiuixTheme.colorScheme.onBackground) {
    val context = LocalContext.current.applicationContext
    val loaded by produceState<LoadedArtwork?>(null, artworkLocator) {
        value = null
        val locator = artworkLocator ?: return@produceState
        value = withContext(Dispatchers.IO) {
            (artworkImages.get(locator) ?: decodeArtwork(context, locator)?.also { artworkImages.put(locator, it) })
                ?.let { LoadedArtwork(locator, it) }
        }
    }
    val bitmap = loaded?.takeIf { it.locator == artworkLocator }?.bitmap
    Box(modifier.background(color.copy(alpha = 0.06f)), contentAlignment = Alignment.Center) {
        if (bitmap != null) Image(bitmap.asImageBitmap(), contentDescription = null,
            modifier = Modifier.fillMaxSize().testTag("media-artwork-loaded"), contentScale = ContentScale.Crop)
        else ZivPlaybackGlyph(icon, Modifier.size(32.dp), color = color.copy(alpha = 0.6f))
    }
}

private fun decodeArtwork(context: Context, locator: String): Bitmap? = try {
    val uri = Uri.parse(locator)
    if (uri.scheme !in setOf("content", "file")) null else {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
        if (options.outWidth !in 1..32_768 || options.outHeight !in 1..32_768) null else {
            options.inJustDecodeBounds = false
            options.inSampleSize = 1
            while (maxOf(options.outWidth, options.outHeight) / options.inSampleSize > 768) options.inSampleSize *= 2
            context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
        }
    }
} catch (_: Exception) { null }
