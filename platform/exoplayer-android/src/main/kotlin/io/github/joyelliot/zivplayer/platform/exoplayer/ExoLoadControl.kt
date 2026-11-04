// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer.platform.exoplayer

import androidx.media3.exoplayer.DefaultLoadControl
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions

internal fun playbackLoadControlBuilder(options: PlaybackOptions) = DefaultLoadControl.Builder()
    .setTargetBufferBytes(options.cacheSizeMiB * 1024 * 1024).apply {
        if (options.audioDelayMs < 0) {
            // 提前音频先消费 D 秒；首个 PCM 写出前不能因视频字节缓冲已满而停载。
            setBufferDurationsMsForLocalPlayback(-options.audioDelayMs + 1000,
                DefaultLoadControl.DEFAULT_MAX_BUFFER_MS,
                DefaultLoadControl.DEFAULT_BUFFER_FOR_PLAYBACK_FOR_LOCAL_PLAYBACK_MS,
                DefaultLoadControl.DEFAULT_BUFFER_FOR_PLAYBACK_AFTER_REBUFFER_FOR_LOCAL_PLAYBACK_MS)
            setPrioritizeTimeOverSizeThresholdsForLocalPlayback(true)
            setPrioritizeTimeOverSizeThresholdsForStreaming(true)
        }
    }
