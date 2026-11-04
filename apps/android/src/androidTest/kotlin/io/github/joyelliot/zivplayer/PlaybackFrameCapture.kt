// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.graphics.Bitmap
import android.os.Handler
import android.os.HandlerThread
import android.view.PixelCopy
import android.view.TextureView
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** 从当前视频输出取像素，不能把保留的 TextureView 旧帧当作 SurfaceView 播放证据。 */
internal fun capturePlaybackFrame(texture: TextureView, width: Int = texture.width, height: Int = texture.height): Bitmap? {
    if (width <= 0 || height <= 0) return null
    val viewport = texture.parent as? VideoViewport
    if (viewport?.directOutputActive != true) return if (texture.isAvailable) texture.getBitmap(width, height) else null
    val surface = viewport.surfaceView.holder.surface
    if (!surface.isValid) return null
    val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
    val thread = HandlerThread("video-pixel-copy").apply { start() }
    val completed = CountDownLatch(1)
    var result = PixelCopy.ERROR_UNKNOWN
    try {
        PixelCopy.request(surface, bitmap, { result = it; completed.countDown() }, Handler(thread.looper))
        if (!completed.await(2, TimeUnit.SECONDS)) {
            // 复制可能仍使用 bitmap；不提前回收，也不将超时当作成功帧。
            return null
        }
        if (result == PixelCopy.SUCCESS) return bitmap
        bitmap.recycle()
        return null
    } finally { thread.quitSafely() }
}

internal fun playbackTextureTimestamp(texture: TextureView): Long? =
    if ((texture.parent as? VideoViewport)?.directOutputActive == true) null else texture.surfaceTexture?.timestamp
