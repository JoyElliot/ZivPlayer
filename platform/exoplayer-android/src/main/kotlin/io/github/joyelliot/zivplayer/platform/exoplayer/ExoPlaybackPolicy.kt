// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.exoplayer

import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.RenderQuality
import io.github.joyelliot.zivplayer.core.model.ToneMappingMode

/** 只按已知格式选择解析器，不把未知字幕伪装成 SRT。 */
internal fun subtitleMimeType(locator: String, title: String?): String {
    val extension = locator.substringBefore('?').substringBefore('#').substringAfterLast('.', "").lowercase()
    val fallback = title?.substringAfterLast('.', "")?.lowercase()
    fun mime(value: String?) = when (value) {
        "srt" -> "application/x-subrip"
        "ass", "ssa" -> "text/x-ssa"
        "vtt" -> "text/vtt"
        "ttml", "xml" -> "application/ttml+xml"
        else -> null
    }
    return mime(extension) ?: mime(fallback) ?: error("此字幕格式暂不支持，请使用 SRT、ASS、SSA 或 VTT。")
}

internal fun unsupportedSettings(options: PlaybackOptions): String? {
    val unsupported = buildList {
        if (options.quality != RenderQuality.BALANCED) add("自定义缩放算法")
        if (options.toneMapping != ToneMappingMode.AUTOMATIC) add("指定色调映射算法")
        if (options.shaders.ids.isNotEmpty()) add("视频 shader")
    }
    return unsupported.takeIf { it.isNotEmpty() }?.joinToString("、", "当前播放器暂不应用：", "。原设置已保留。")
}
