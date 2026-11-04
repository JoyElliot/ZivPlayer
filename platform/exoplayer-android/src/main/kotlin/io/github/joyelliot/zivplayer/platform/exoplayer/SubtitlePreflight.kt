// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import android.net.Uri
import androidx.media3.common.Format
import androidx.media3.extractor.text.CueEncoder
import androidx.media3.extractor.text.DefaultSubtitleParserFactory
import androidx.media3.extractor.text.SubtitleParser
import io.github.joyelliot.zivplayer.core.model.SubtitleSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/** 在修改播放源之前检查受管字幕，读取/解析失败只拒绝命令。 */
internal suspend fun validateSubtitle(context: Context, source: SubtitleSource) = withContext(Dispatchers.IO) {
    val mime = subtitleMimeType(source.locator, source.title)
    val data = checkNotNull(context.contentResolver.openInputStream(Uri.parse(source.locator))).use { input ->
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val size = input.read(buffer)
            if (size < 0) break
            check(output.size() + size <= 8 * 1024 * 1024) { "The subtitle is too large." }
            output.write(buffer, 0, size)
        }
        output.toByteArray()
    }
    validateSubtitleBytes(data, mime)
}

internal fun validateSubtitleBytes(data: ByteArray, mime: String) {
    require(data.isNotEmpty()) { "The subtitle is empty." }
    val parser = DefaultSubtitleParserFactory().create(Format.Builder().setSampleMimeType(mime).build())
    val encoder = CueEncoder()
    var hasCues = false
    try {
        parser.parse(data, SubtitleParser.OutputOptions.allCues()) { timed ->
            encoder.encode(timed.cues, timed.durationUs)
            hasCues = hasCues || timed.cues.isNotEmpty()
        }
        require(hasCues) { "The subtitle contains no readable cues." }
    } finally { parser.reset() }
}
