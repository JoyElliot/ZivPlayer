// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.Context
import android.net.Uri
import android.os.CancellationSignal
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import java.io.OutputStream
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.withTimeout

/** Exports an explicitly captured sample, without the media URI, filename, or imported paths. */
class DiagnosticsExporter(context: Context) {
    private val resolver = context.applicationContext.contentResolver
    private val writer = BoundedBlockingIo("ZivDiagnosticsExport")

    suspend fun write(uri: Uri, json: String) = withTimeout(15_000L) {
        require(uri.scheme == "content" && json.toByteArray(Charsets.UTF_8).size <= 16_384)
        val cancellation = CancellationSignal()
        val output = AtomicReference<OutputStream?>()
        writer.run(onCancel = { cancellation.cancel(); runCatching { output.getAndSet(null)?.close() } }) { active ->
            checkNotNull(resolver.openAssetFileDescriptor(uri, "wt", cancellation)).use { descriptor ->
                descriptor.createOutputStream().use { stream ->
                    output.set(stream)
                    check(active()) { "Export was cancelled." }
                    stream.write(json.toByteArray(Charsets.UTF_8))
                    stream.flush()
                    output.set(null)
                }
            }
        }
    }
}

internal fun String.jsonQuoted(): String = buildString {
    append('"')
    for (character in this@jsonQuoted.take(256)) when (character) {
        '"' -> append("\\\"")
        '\\' -> append("\\\\")
        '\n' -> append("\\n")
        '\r' -> append("\\r")
        '\t' -> append("\\t")
        else -> if (character.code < 32) append("\\u%04x".format(character.code)) else append(character)
    }
    append('"')
}

fun PlaybackDiagnostics.toDiagnosticJson(sampledAtEpochMs: Long, exportedAtEpochMs: Long): String {
    val fields = linkedMapOf<String, Any?>(
        "schemaVersion" to 1, "sampledAtEpochMs" to sampledAtEpochMs, "exportedAtEpochMs" to exportedAtEpochMs,
        "videoCodec" to videoCodec, "audioCodec" to audioCodec, "decoder" to decoder,
        "videoWidth" to videoWidth, "videoHeight" to videoHeight,
        "sourceFramesPerSecond" to sourceFramesPerSecond, "displayFramesPerSecond" to displayFramesPerSecond,
        "droppedFrames" to droppedFrames, "decoderDroppedFrames" to decoderDroppedFrames,
        "cacheUsedBytes" to cacheUsedBytes, "cacheDurationSeconds" to cacheDurationSeconds,
        "audioVideoSyncSeconds" to audioVideoSyncSeconds, "colorPrimaries" to colorPrimaries,
        "transferFunction" to transferFunction, "pixelFormat" to pixelFormat,
        "videoBitrate" to videoBitrate, "audioBitrate" to audioBitrate,
        "engineVersion" to engineVersion, "ffmpegVersion" to ffmpegVersion,
    )
    return fields.entries.joinToString(",\n", "{\n", "\n}\n") { (key, value) ->
        "  ${key.jsonQuoted()}: " + when (value) {
            null -> "null"
            is String -> value.jsonQuoted()
            is Double -> if (value.isFinite()) value.toString() else "null"
            else -> value.toString()
        }
    }
}
