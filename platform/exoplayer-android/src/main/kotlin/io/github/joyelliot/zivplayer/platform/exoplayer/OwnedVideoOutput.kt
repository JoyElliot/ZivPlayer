// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.exoplayer

import android.os.Parcel
import android.view.Surface

/** 每次连接使用不同的 Surface 句柄身份，避免 A→B→A 的旧首帧回调命中新连接。 */
internal class OwnedVideoOutput(val source: Surface) : AutoCloseable {
    // Parcelable 复制同一 BufferQueue 的句柄，不复制视频像素，也不创建渲染中间层。
    val surface: Surface = Parcel.obtain().let { parcel ->
        try {
            source.writeToParcel(parcel, 0)
            parcel.setDataPosition(0)
            checkNotNull(Surface.CREATOR.createFromParcel(parcel))
        } finally { parcel.recycle() }
    }

    fun accepts(output: Any): Boolean = output === surface
    override fun close() { surface.release() }
}
