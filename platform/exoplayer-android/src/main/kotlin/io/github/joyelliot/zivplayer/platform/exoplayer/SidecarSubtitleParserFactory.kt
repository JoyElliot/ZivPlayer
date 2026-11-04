// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.util.Consumer
import androidx.media3.extractor.text.CuesWithTiming
import androidx.media3.extractor.text.DefaultSubtitleParserFactory
import androidx.media3.extractor.text.SubtitleParser

/** 仅适配整文件外挂字幕；内嵌字幕仍使用原解析器和逐样本语义。 */
internal class SidecarSubtitleParserFactory(
    private val externalIds: Set<String>,
    private val delegate: SubtitleParser.Factory = DefaultSubtitleParserFactory(),
) : SubtitleParser.Factory by delegate {
    override fun create(format: Format): SubtitleParser {
        val parser = delegate.create(format)
        return if (format.id in externalIds) EndMarkedSubtitleParser(parser) else parser
    }
}

/**
 * Media3 1.11.1 的 lazy 外挂源未消费 TrackOutput.durationUs，按最后 sample 起点估算片尾。
 * 在最后一个有限 cue 的结束处补空样本，保留按需读取，同时避免末条中段 seek 被误判为 EOS。
 */
internal class EndMarkedSubtitleParser(private val delegate: SubtitleParser) : SubtitleParser by delegate {
    override fun parse(data: ByteArray, outputOptions: SubtitleParser.OutputOptions,
        output: Consumer<CuesWithTiming>) = parse(data, 0, data.size, outputOptions, output)

    override fun parse(data: ByteArray, offset: Int, length: Int, outputOptions: SubtitleParser.OutputOptions,
        output: Consumer<CuesWithTiming>) {
        // onlyCuesAfter 不能证明未输出部分的最终时刻；外置 extractor 使用 all 或 after-then-before。
        if (outputOptions.startTimeUs != C.TIME_UNSET && !outputOptions.outputAllCues) {
            delegate.parse(data, offset, length, outputOptions, output)
            return
        }
        val cues = mutableListOf<CuesWithTiming>()
        delegate.parse(data, offset, length, outputOptions) { cues += it }
        if (cues.isNotEmpty() && cues.all { it.endTimeUs != C.TIME_UNSET && it.durationUs >= 0 }) {
            val end = cues.maxOf { it.endTimeUs }
            val start = outputOptions.startTimeUs
            // 保留原解析器对跨 seek 字幕的次序，不能把它排在未来的空尾样本之后。
            val beforeIndex = if (start != C.TIME_UNSET && end >= start)
                cues.indexOfFirst { it.endTimeUs < start }.takeIf { it >= 0 } else null
            cues.add(beforeIndex ?: cues.size, CuesWithTiming(emptyList(), end, 0))
        }
        cues.forEach(output::accept)
    }
}
