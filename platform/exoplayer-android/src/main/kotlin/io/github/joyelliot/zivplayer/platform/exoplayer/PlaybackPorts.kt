// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.view.Surface
import androidx.media3.common.text.CueGroup
import io.github.joyelliot.zivplayer.core.model.PlaybackDiagnostics
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import kotlinx.coroutines.flow.StateFlow

/** 输出租约属于一个后端，旧视图的销毁回调不能清除新视图。 */
interface PlaybackSurfacePort {
    /** 视图销毁回调必须在返回前解绑；只能从主线程调用，不等待媒体/配置命令。 */
    fun attachSurfaceNow(surface: Surface): PlaybackSurfaceLease
    fun detachSurfaceNow(lease: PlaybackSurfaceLease)
    suspend fun attachSurface(surface: Surface): PlaybackSurfaceLease
    suspend fun detachSurface(lease: PlaybackSurfaceLease)
    suspend fun resizeSurface(lease: PlaybackSurfaceLease, width: Int, height: Int)
}

class PlaybackSurfaceLease internal constructor(internal val owner: Any, internal val token: Long)

data class PlaybackResourcePaths(val subtitleFontsDirectory: String? = null, val shaderPaths: List<String> = emptyList())

interface PlaybackConfigurationPort {
    /** 返回不能应用的旧设置说明；不删除用户保存的偏好。 */
    suspend fun configure(options: PlaybackOptions, resources: PlaybackResourcePaths): String?
    suspend fun readDiagnostics(): PlaybackDiagnostics
}

/** 字幕留在 Android 层；携带队列项身份，避免切片时显示上一个文件的字幕。 */
data class PlaybackCaptions(val itemId: QueueItemId? = null, val cues: CueGroup = CueGroup.EMPTY_TIME_ZERO)

interface PlaybackCaptionPort {
    val captions: StateFlow<PlaybackCaptions>
}
