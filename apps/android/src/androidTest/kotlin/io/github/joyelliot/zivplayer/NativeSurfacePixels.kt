// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.view.PixelCopy
import android.view.SurfaceView
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** Producer-buffer readback; use UiAutomation screenshots for final screen composition. */
internal fun copyNativeSurface(view: SurfaceView): Bitmap? {
    if (!view.holder.surface.isValid || view.width <= 0 || view.height <= 0) return null
    val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
    val result = CompletableFuture<Int>()
    try {
        PixelCopy.request(view, bitmap, { result.complete(it) }, Handler(Looper.getMainLooper()))
    } catch (_: IllegalArgumentException) {
        bitmap.recycle()
        return null
    }
    val code = try { result.get(5, TimeUnit.SECONDS) } catch (error: Exception) {
        result.whenComplete { _, _ -> bitmap.recycle() }
        throw error
    }
    if (code == PixelCopy.SUCCESS) return bitmap
    bitmap.recycle()
    return null
}
