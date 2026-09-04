// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.view.Surface

/**
 * Narrow, injectable transport used by [SourceMpvClient].
 *
 * Native methods return the original libmpv status wherever one exists. The
 * event packet is already detached from libmpv-owned memory before this API
 * returns; tests can therefore exercise the complete Kotlin lifecycle without
 * loading a shared library.
 */
internal interface MpvNativeApi {
    fun create(applicationContext: Context): Long

    fun setOptionString(token: Long, name: String, value: String): Int

    fun initialize(token: Long): Int

    fun command(token: Long, arguments: Array<String>): Int

    fun getPropertyDouble(token: Long, name: String, output: DoubleArray): Int

    fun getPropertyBoolean(token: Long, name: String, output: IntArray): Int

    fun setPropertyDouble(token: Long, name: String, value: Double): Int

    fun setPropertyBoolean(token: Long, name: String, value: Boolean): Int

    fun setPropertyString(token: Long, name: String, value: String): Int

    fun observeProperty(
        token: Long,
        name: String,
        rawFormat: Int,
        replyUserdata: Long,
    ): Int

    fun unobserveProperty(token: Long, replyUserdata: Long): Int

    fun waitEvent(token: Long, timeoutSeconds: Double): MpvNativeEvent

    fun wakeup(token: Long): Int

    fun attachSurface(token: Long, surface: Surface): Int

    fun detachSurface(token: Long): Int

    /**
     * Removes [token] from the native registry and terminates its handle.
     *
     * A negative status is a pre-commit failure and is safe to retry. A
     * production implementation must not return a status after committing the
     * registry removal or native termination. If this call throws, the Kotlin
     * owner treats the commit point as unknowable and never invokes it again.
     */
    fun terminateDestroy(token: Long): Int
}

internal data class MpvNativeProperty(
    val name: String,
    val rawFormat: Int,
    val hasValue: Boolean,
    val flagValue: Boolean = false,
    val int64Value: Long = 0L,
    val doubleValue: Double = 0.0,
    val stringValue: String? = null,
)

/** Immutable copy of one event returned by mpv_wait_event(). */
internal data class MpvNativeEvent(
    val rawEventId: Int,
    val errorCode: Int = 0,
    val replyUserdata: Long? = null,
    val property: MpvNativeProperty? = null,
    val playlistEntryId: Long? = null,
    val rawEndReason: Int? = null,
    val endErrorCode: Int = 0,
    val playlistInsertId: Long? = null,
    val playlistInsertCount: Int = 0,
)

internal const val MPV_ERROR_PROPERTY_UNAVAILABLE = -10
