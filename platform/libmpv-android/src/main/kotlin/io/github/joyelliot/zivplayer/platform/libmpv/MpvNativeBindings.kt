// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.view.Surface

/** JNI transport for the source-built libzivplayer_mpv wrapper. */
internal object MpvNativeBindings : MpvNativeApi {
    private val libraryLoaded by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        System.loadLibrary(LIBRARY_NAME)
        true
    }

    override fun create(applicationContext: Context): Long {
        ensureLoaded()
        return nativeCreate(applicationContext)
    }

    override fun setOptionString(token: Long, name: String, value: String): Int {
        ensureLoaded()
        return nativeSetOptionString(token, name, value)
    }

    override fun initialize(token: Long): Int {
        ensureLoaded()
        return nativeInitialize(token)
    }

    override fun command(token: Long, arguments: Array<String>): Int {
        ensureLoaded()
        return nativeCommand(token, arguments)
    }

    override fun getPropertyDouble(token: Long, name: String, output: DoubleArray): Int {
        require(output.size == 1) { "The double output buffer must contain exactly one slot." }
        ensureLoaded()
        return nativeGetPropertyDouble(token, name, output)
    }

    override fun getPropertyBoolean(token: Long, name: String, output: IntArray): Int {
        require(output.size == 1) { "The boolean output buffer must contain exactly one slot." }
        ensureLoaded()
        return nativeGetPropertyBoolean(token, name, output)
    }

    override fun setPropertyDouble(token: Long, name: String, value: Double): Int {
        ensureLoaded()
        return nativeSetPropertyDouble(token, name, value)
    }

    override fun setPropertyBoolean(token: Long, name: String, value: Boolean): Int {
        ensureLoaded()
        return nativeSetPropertyBoolean(token, name, value)
    }

    override fun setPropertyString(token: Long, name: String, value: String): Int {
        ensureLoaded()
        return nativeSetPropertyString(token, name, value)
    }

    override fun observeProperty(
        token: Long,
        name: String,
        rawFormat: Int,
        replyUserdata: Long,
    ): Int {
        ensureLoaded()
        return nativeObserveProperty(token, name, rawFormat, replyUserdata)
    }

    override fun unobserveProperty(token: Long, replyUserdata: Long): Int {
        ensureLoaded()
        return nativeUnobserveProperty(token, replyUserdata)
    }

    override fun waitEvent(token: Long, timeoutSeconds: Double): MpvNativeEvent {
        val integers = IntArray(INTEGER_FIELD_COUNT)
        val longs = LongArray(LONG_FIELD_COUNT)
        val doubles = DoubleArray(DOUBLE_FIELD_COUNT)
        val strings = arrayOfNulls<String>(STRING_FIELD_COUNT)
        ensureLoaded()
        val status = nativeWaitEvent(token, timeoutSeconds, integers, longs, doubles, strings)
        requireMpvSuccess("wait for event", status)
        return decodeEvent(integers, longs, doubles, strings)
    }

    override fun wakeup(token: Long): Int {
        ensureLoaded()
        return nativeWakeup(token)
    }

    override fun attachSurface(token: Long, surface: Surface): Int {
        ensureLoaded()
        return nativeAttachSurface(token, surface)
    }

    override fun detachSurface(token: Long): Int {
        ensureLoaded()
        return nativeDetachSurface(token)
    }

    override fun terminateDestroy(token: Long): Int {
        ensureLoaded()
        return nativeTerminateDestroy(token)
    }

    private fun ensureLoaded() {
        libraryLoaded
    }

    private external fun nativeCreate(applicationContext: Context): Long

    private external fun nativeSetOptionString(token: Long, name: String, value: String): Int

    private external fun nativeInitialize(token: Long): Int

    private external fun nativeCommand(token: Long, arguments: Array<String>): Int

    private external fun nativeGetPropertyDouble(
        token: Long,
        name: String,
        output: DoubleArray,
    ): Int

    private external fun nativeGetPropertyBoolean(
        token: Long,
        name: String,
        output: IntArray,
    ): Int

    private external fun nativeSetPropertyDouble(token: Long, name: String, value: Double): Int

    private external fun nativeSetPropertyBoolean(token: Long, name: String, value: Boolean): Int

    private external fun nativeSetPropertyString(token: Long, name: String, value: String): Int

    private external fun nativeObserveProperty(
        token: Long,
        name: String,
        rawFormat: Int,
        replyUserdata: Long,
    ): Int

    private external fun nativeUnobserveProperty(token: Long, replyUserdata: Long): Int

    private external fun nativeWaitEvent(
        token: Long,
        timeoutSeconds: Double,
        integers: IntArray,
        longs: LongArray,
        doubles: DoubleArray,
        strings: Array<String?>,
    ): Int

    private external fun nativeWakeup(token: Long): Int

    private external fun nativeAttachSurface(token: Long, surface: Surface): Int

    private external fun nativeDetachSurface(token: Long): Int

    private external fun nativeTerminateDestroy(token: Long): Int

    private const val LIBRARY_NAME = "zivplayer_mpv"

    internal const val INTEGER_FIELD_COUNT = 8
    internal const val LONG_FIELD_COUNT = 4
    internal const val DOUBLE_FIELD_COUNT = 1
    internal const val STRING_FIELD_COUNT = 2

    internal const val INTEGER_EVENT_ID = 0
    internal const val INTEGER_EVENT_ERROR = 1
    internal const val INTEGER_PROPERTY_FORMAT = 2
    internal const val INTEGER_END_REASON = 3
    internal const val INTEGER_END_ERROR = 4
    internal const val INTEGER_INSERT_COUNT = 5
    internal const val INTEGER_PRESENCE_FLAGS = 6
    internal const val INTEGER_PROPERTY_FLAG = 7

    internal const val LONG_REPLY_USERDATA = 0
    internal const val LONG_PLAYLIST_ENTRY_ID = 1
    internal const val LONG_PLAYLIST_INSERT_ID = 2
    internal const val LONG_PROPERTY_INT64 = 3

    internal const val DOUBLE_PROPERTY_VALUE = 0

    internal const val STRING_PROPERTY_NAME = 0
    internal const val STRING_PROPERTY_VALUE = 1

    internal const val PRESENCE_REPLY_USERDATA = 1 shl 0
    internal const val PRESENCE_PLAYLIST_ENTRY_ID = 1 shl 1
    internal const val PRESENCE_END_REASON = 1 shl 2
    internal const val PRESENCE_PLAYLIST_INSERT_ID = 1 shl 3
    internal const val PRESENCE_PROPERTY_VALUE = 1 shl 4

    internal fun decodeEvent(
        integers: IntArray,
        longs: LongArray,
        doubles: DoubleArray,
        strings: Array<String?>,
    ): MpvNativeEvent {
        require(integers.size == INTEGER_FIELD_COUNT) { "Malformed native integer event buffer." }
        require(longs.size == LONG_FIELD_COUNT) { "Malformed native long event buffer." }
        require(doubles.size == DOUBLE_FIELD_COUNT) { "Malformed native double event buffer." }
        require(strings.size == STRING_FIELD_COUNT) { "Malformed native string event buffer." }

        val presence = integers[INTEGER_PRESENCE_FLAGS]
        val rawEventId = integers[INTEGER_EVENT_ID]
        val property = if (rawEventId == MpvEventType.PROPERTY_CHANGE.rawValue) {
            MpvNativeProperty(
                name = requireNotNull(strings[STRING_PROPERTY_NAME]) {
                    "A native property-change event omitted its property name."
                },
                rawFormat = integers[INTEGER_PROPERTY_FORMAT],
                hasValue = presence has PRESENCE_PROPERTY_VALUE,
                flagValue = integers[INTEGER_PROPERTY_FLAG] != 0,
                int64Value = longs[LONG_PROPERTY_INT64],
                doubleValue = doubles[DOUBLE_PROPERTY_VALUE],
                stringValue = strings[STRING_PROPERTY_VALUE],
            )
        } else {
            null
        }
        return MpvNativeEvent(
            rawEventId = rawEventId,
            errorCode = integers[INTEGER_EVENT_ERROR],
            replyUserdata = longs[LONG_REPLY_USERDATA]
                .takeIf { presence has PRESENCE_REPLY_USERDATA },
            property = property,
            playlistEntryId = longs[LONG_PLAYLIST_ENTRY_ID]
                .takeIf { presence has PRESENCE_PLAYLIST_ENTRY_ID },
            rawEndReason = integers[INTEGER_END_REASON]
                .takeIf { presence has PRESENCE_END_REASON },
            endErrorCode = integers[INTEGER_END_ERROR],
            playlistInsertId = longs[LONG_PLAYLIST_INSERT_ID]
                .takeIf { presence has PRESENCE_PLAYLIST_INSERT_ID },
            playlistInsertCount = integers[INTEGER_INSERT_COUNT],
        )
    }

    private infix fun Int.has(flag: Int): Boolean = this and flag != 0
}

internal fun requireMpvSuccess(operation: String, status: Int): Int {
    if (status < 0) {
        throw MpvOperationException(operation, status)
    }
    return status
}
