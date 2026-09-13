// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.data.media

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import android.util.AtomicFile
import io.github.joyelliot.zivplayer.core.media.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import kotlin.math.max

/** Bounded, regenerable local cache, separate from personal records and the playback decoder. */
class LocalMediaDetailsStore(context: Context) : MediaDetailsRepository {
    private val context = context.applicationContext
    private val resolver = context.contentResolver
    private val directory = File(context.cacheDir, "media-details")
    private val io = BoundedBlockingIo("ZivMediaDetails")
    private val gate = Mutex()
    private data class Entry(val request: MediaDetailsRequest, val key: String, val details: MediaDetails, val readAt: Long)
    private val entries = LinkedHashMap<String, Entry>()
    private val mutableDetails = MutableStateFlow<Map<String, MediaDetails>>(emptyMap())
    override val details = mutableDetails.asStateFlow()

    override suspend fun load(request: MediaDetailsRequest): MediaDetails? = withContext(Dispatchers.IO) { withTimeoutOrNull(8_000) { gate.withLock {
        val uri = request.sourceUri.value
        entries[uri]?.takeIf {
            (request.sizeBytes == null || request.sizeBytes == it.details.sizeBytes) &&
                (request.fileName == null || request.fileName == it.details.fileName) &&
                (request.mimeType == null || request.mimeType == it.details.mimeType) &&
                (request.modifiedEpochMs == null || request.modifiedEpochMs == it.request.modifiedEpochMs) &&
                System.currentTimeMillis() - it.readAt in 0 until CACHE_AGE_MS &&
                (it.details.artworkLocator == null || validArtwork(File(directory, "${it.key}.jpg")))
        }?.let { return@withLock it.details }
        try {
            val entry = withTimeout(8_000) { io.run { active -> read(request, active) } }
            entries.remove(uri)
            entries[uri] = entry
            while (entries.size > 512) entries.remove(entries.keys.first())
            mutableDetails.value = entries.mapValues { it.value.details }
            entry.details
        } catch (_: TimeoutCancellationException) {
            null
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            null
        }
    } } }

    private fun read(request: MediaDetailsRequest, active: () -> Boolean): Entry {
        check(active())
        val uri = Uri.parse(request.sourceUri.value)
        require(uri.scheme == "content" || uri.scheme == "file")
        check(directory.isDirectory || directory.mkdirs())
        var fileName = request.fileName
        var size = request.sizeBytes
        if (fileName == null || size == null) runCatching {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                    if (nameIndex >= 0 && !cursor.isNull(nameIndex)) fileName = fileName ?: clean(cursor.getString(nameIndex))
                    if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) size = size ?: cursor.getLong(sizeIndex).takeIf { it >= 0 }
                }
            }
        }
        val mime = request.mimeType ?: runCatching { resolver.getType(uri) }.getOrNull()
        val key = digest(org.json.JSONArray(listOf(CACHE_VERSION, request.sourceUri.value,
            fileName, mime, size, request.modifiedEpochMs)).toString())
        val jsonFile = File(directory, "$key.json")
        val imageFile = File(directory, "$key.jpg")
        val backup = File(jsonFile.path + ".bak")
        if ((jsonFile.isFile || backup.isFile) &&
            System.currentTimeMillis() - maxOf(jsonFile.lastModified(), backup.lastModified()) in 0 until CACHE_AGE_MS) {
            runCatching {
                val json = AtomicFile(jsonFile).openRead().use { input ->
                    check(jsonFile.length() in 1..32_768)
                    input.bufferedReader().readText()
                }
                val stored = JSONObject(json)
                val readAt = stored.optLong("readAt", 0L)
                check(System.currentTimeMillis() - readAt in 0 until CACHE_AGE_MS)
                val data = decode(stored)
                check(data.artworkLocator == null || validArtwork(imageFile))
                jsonFile.setLastModified(System.currentTimeMillis())
                if (imageFile.isFile) imageFile.setLastModified(System.currentTimeMillis())
                return Entry(request, key, data, readAt)
            }
        }
        check(active())
        var data = MediaDetails(fileName = fileName, mimeType = mime, sizeBytes = size)
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(context, uri)
            fun text(key: Int) = runCatching { clean(retriever.extractMetadata(key)) }.getOrNull()
            data = data.copy(
                title = text(MediaMetadataRetriever.METADATA_KEY_TITLE),
                artist = text(MediaMetadataRetriever.METADATA_KEY_ARTIST),
                album = text(MediaMetadataRetriever.METADATA_KEY_ALBUM),
                albumArtist = text(MediaMetadataRetriever.METADATA_KEY_ALBUMARTIST),
                year = text(MediaMetadataRetriever.METADATA_KEY_YEAR) ?: text(MediaMetadataRetriever.METADATA_KEY_DATE)?.take(4),
                genre = text(MediaMetadataRetriever.METADATA_KEY_GENRE),
                trackNumber = text(MediaMetadataRetriever.METADATA_KEY_CD_TRACK_NUMBER),
                discNumber = text(MediaMetadataRetriever.METADATA_KEY_DISC_NUMBER),
                durationMs = text(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()?.takeIf { it > 0 },
                videoWidth = text(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull()?.takeIf { it > 0 },
                videoHeight = text(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull()?.takeIf { it > 0 },
                rotationDegrees = text(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull(),
                bitrate = text(MediaMetadataRetriever.METADATA_KEY_BITRATE)?.toLongOrNull()?.takeIf { it > 0 },
            )
            check(active())
            var artwork = runCatching { retriever.embeddedPicture?.takeIf { it.size <= 8 * 1024 * 1024 }?.let(::decodeArtwork) }.getOrNull()
            if (artwork == null && text(MediaMetadataRetriever.METADATA_KEY_HAS_VIDEO) != null) {
                val positionUs = ((data.durationMs ?: 10_000) / 10).coerceIn(0, 30_000) * 1000
                artwork = runCatching {
                    if (Build.VERSION.SDK_INT >= 27) retriever.getScaledFrameAtTime(positionUs,
                        MediaMetadataRetriever.OPTION_CLOSEST_SYNC, 768, 768)
                    else retriever.getFrameAtTime(positionUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)?.let(::scaledArtwork)
                }.getOrNull()
            }
            artwork?.let { bitmap ->
                try {
                    check(active())
                    writeAtomic(imageFile) { stream -> check(bitmap.compress(Bitmap.CompressFormat.JPEG, 90, stream)) }
                    data = data.copy(artworkLocator = Uri.Builder().scheme("content")
                        .authority("${context.packageName}.resources").appendPath("artwork").appendPath(imageFile.name)
                        .appendQueryParameter("v", System.currentTimeMillis().toString()).build().toString())
                } finally { bitmap.recycle() }
            }
        } catch (_: Exception) {
            // A playable container may not have a platform metadata/thumbnail implementation.
        } finally { runCatching { retriever.release() } }
        check(active())
        val extractor = MediaExtractor()
        try {
            extractor.setDataSource(context, uri, null)
            for (index in 0 until extractor.trackCount) {
                check(active())
                val format = extractor.getTrackFormat(index)
                val type = format.getString(MediaFormat.KEY_MIME) ?: continue
                if (type.startsWith("video/") && data.videoCodec == null) data = data.copy(
                    videoCodec = type, videoWidth = data.videoWidth ?: format.int(MediaFormat.KEY_WIDTH),
                    videoHeight = data.videoHeight ?: format.int(MediaFormat.KEY_HEIGHT),
                    rotationDegrees = data.rotationDegrees ?: format.int(MediaFormat.KEY_ROTATION),
                )
                if (type.startsWith("audio/") && data.audioCodec == null) data = data.copy(
                    audioCodec = type, sampleRate = format.int(MediaFormat.KEY_SAMPLE_RATE),
                    channelCount = format.int(MediaFormat.KEY_CHANNEL_COUNT),
                    bitrate = data.bitrate ?: if (data.videoCodec == null) format.int(MediaFormat.KEY_BIT_RATE)?.toLong() else null,
                )
                if (data.durationMs == null) data = data.copy(durationMs = format.long(MediaFormat.KEY_DURATION)?.div(1000)?.takeIf { it > 0 })
            }
        } catch (_: Exception) {
            // Keep the fields already obtained; absence is not reported as zero or a file failure.
        } finally { runCatching { extractor.release() } }
        check(active())
        val readAt = System.currentTimeMillis()
        writeAtomic(jsonFile) { it.write(encode(data).put("readAt", readAt).toString().toByteArray(Charsets.UTF_8)) }
        trimDiskCache(key)
        return Entry(request, key, data, readAt)
    }

    private fun decodeArtwork(bytes: ByteArray): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || bounds.outWidth > 32_768 || bounds.outHeight > 32_768) return null
        val options = BitmapFactory.Options()
        while (max(bounds.outWidth, bounds.outHeight) / options.inSampleSize.coerceAtLeast(1) > 1536)
            options.inSampleSize = options.inSampleSize.coerceAtLeast(1) * 2
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)?.let(::scaledArtwork)
    }

    private fun scaledArtwork(bitmap: Bitmap): Bitmap {
        val longSide = max(bitmap.width, bitmap.height)
        if (longSide <= 768) return bitmap
        val result = Bitmap.createScaledBitmap(bitmap, (bitmap.width * 768L / longSide).toInt().coerceAtLeast(1),
            (bitmap.height * 768L / longSide).toInt().coerceAtLeast(1), true)
        if (result !== bitmap) bitmap.recycle()
        return result
    }

    private fun trimDiskCache(retainKey: String) {
        val files = directory.listFiles()?.filter { it.isFile }.orEmpty()
        var size = files.sumOf(File::length)
        var count = files.size
        for (file in files.sortedBy(File::lastModified)) {
            if (size <= 64 * 1024 * 1024 && count <= 1024) break
            if (file.name.startsWith(retainKey)) continue
            val bytes = file.length()
            if (file.delete()) { size -= bytes; count-- }
        }
    }

    private fun writeAtomic(file: File, write: (java.io.FileOutputStream) -> Unit) {
        val atomic = AtomicFile(file)
        val stream = atomic.startWrite()
        try { write(stream); atomic.finishWrite(stream) } catch (failure: Exception) { atomic.failWrite(stream); throw failure }
    }

    private fun validArtwork(file: File): Boolean = runCatching {
        AtomicFile(file).openRead().use { input ->
            check(file.length() in 4..4 * 1024 * 1024)
            val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeStream(input, null, options)
            check(options.outWidth in 1..768 && options.outHeight in 1..768)
        }
        java.io.RandomAccessFile(file, "r").use { input ->
            input.seek(input.length() - 2)
            check(input.readUnsignedShort() == 0xffd9)
        }
        true
    }.getOrDefault(false)

    private fun encode(d: MediaDetails): JSONObject = JSONObject().apply {
        put("fileName", d.fileName); put("mimeType", d.mimeType); put("sizeBytes", d.sizeBytes)
        put("title", d.title); put("artist", d.artist); put("album", d.album); put("albumArtist", d.albumArtist)
        put("year", d.year); put("genre", d.genre); put("trackNumber", d.trackNumber); put("discNumber", d.discNumber)
        put("durationMs", d.durationMs); put("videoWidth", d.videoWidth); put("videoHeight", d.videoHeight)
        put("rotationDegrees", d.rotationDegrees); put("bitrate", d.bitrate); put("videoCodec", d.videoCodec)
        put("audioCodec", d.audioCodec); put("sampleRate", d.sampleRate); put("channelCount", d.channelCount)
        put("artworkLocator", d.artworkLocator)
    }
    private fun decode(j: JSONObject) = MediaDetails(
        fileName = j.text("fileName"), mimeType = j.text("mimeType"), sizeBytes = j.number("sizeBytes"),
        title = j.text("title"), artist = j.text("artist"), album = j.text("album"), albumArtist = j.text("albumArtist"),
        year = j.text("year"), genre = j.text("genre"), trackNumber = j.text("trackNumber"), discNumber = j.text("discNumber"),
        durationMs = j.number("durationMs"), videoWidth = j.number("videoWidth")?.toInt(), videoHeight = j.number("videoHeight")?.toInt(),
        rotationDegrees = j.number("rotationDegrees")?.toInt(), bitrate = j.number("bitrate"), videoCodec = j.text("videoCodec"),
        audioCodec = j.text("audioCodec"), sampleRate = j.number("sampleRate")?.toInt(), channelCount = j.number("channelCount")?.toInt(),
        artworkLocator = j.text("artworkLocator"),
    )
    private fun JSONObject.text(key: String) = if (has(key) && !isNull(key)) clean(getString(key)) else null
    private fun JSONObject.number(key: String) = if (has(key) && !isNull(key)) getLong(key) else null
    private fun MediaFormat.int(key: String) = runCatching { if (containsKey(key)) getInteger(key).takeIf { it > 0 } else null }.getOrNull()
    private fun MediaFormat.long(key: String) = runCatching { if (containsKey(key)) getLong(key).takeIf { it > 0 } else null }.getOrNull()
    private fun clean(value: String?) = value?.replace('\u0000', ' ')?.trim()?.take(1024)?.takeUnless(String::isBlank)
    private fun digest(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
    private companion object { const val CACHE_VERSION = 2; const val CACHE_AGE_MS = 24 * 60 * 60 * 1000L }
}
