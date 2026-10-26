// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.libvlc

import android.os.Handler
import android.os.Looper

/** One watcher per player. All entry points and close run on the main thread. */
internal class VlcSeekBridge(pointer: Long, private val completed: (Long, Long) -> Unit) : AutoCloseable {
    private val main = Handler(Looper.getMainLooper())
    private var handle = nativeOpen(pointer).also { check(it != 0L) { "LibVLC 4 seek watcher unavailable." } }
    fun seek(token: Long, positionMs: Long, fast: Boolean, requireOutput: Boolean = true) {
        check(handle != 0L && nativeSeek(handle, token, positionMs, fast, requireOutput) == 0) { "LibVLC rejected seek." }
    }
    @Suppress("unused") // Called from the native timer thread; never call a player API here.
    private fun onNativeSeekCompleted(token: Long, positionMs: Long) {
        main.post { if (handle != 0L) completed(token, positionMs) }
    }
    fun positionMs(): Long? = if (handle != 0L) nativePosition(handle).takeIf { it >= 0 } else null
    override fun close() {
        val old = handle
        handle = 0
        if (old != 0L) nativeClose(old)
        main.removeCallbacksAndMessages(null)
    }
    private external fun nativeOpen(pointer: Long): Long
    private external fun nativeSeek(handle: Long, token: Long, positionMs: Long, fast: Boolean, requireOutput: Boolean): Int
    private external fun nativePosition(handle: Long): Long
    private external fun nativeClose(handle: Long)
    companion object { init { System.loadLibrary("zivplayer_vlc4") } }
}
