// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.view.Surface

/**
 * Module-internal libmpv boundary.
 *
 * A source implementation must report every available native status by
 * throwing [MpvOperationException] on failure, keep event ordering intact,
 * and make [destroy] idempotent. Raw mpv identifiers and
 * property names stop at this module.
 *
 * Observer callbacks may arrive on the client's dedicated event thread and
 * must return promptly. The owner serializes ordinary calls, first quiesces
 * new calls under that gate, and then removes observers and destroys the
 * client outside any lock which a callback can acquire. Implementations must
 * still coordinate their event pump with idempotent destruction.
 */
internal interface MpvClient {
    interface Observer {
        fun onPropertyChanged(change: MpvPropertyChange)

        fun onEvent(event: MpvClientEvent)

        fun onFailure(failure: MpvClientFailure) = Unit
    }

    fun addObserver(observer: Observer)

    fun removeObserver(observer: Observer)

    fun setOptionString(name: String, value: String)

    fun initialize()

    fun command(arguments: Array<String>)

    fun getPropertyDouble(name: String): Double?

    fun getPropertyBoolean(name: String): Boolean?

    fun setPropertyDouble(name: String, value: Double)

    fun setPropertyBoolean(name: String, value: Boolean)

    fun setPropertyString(name: String, value: String)

    fun observeProperty(
        name: String,
        format: MpvPropertyFormat,
        replyUserdata: Long,
    )

    /** Returns the number of matching native observations removed. */
    fun unobserveProperty(replyUserdata: Long): Int

    fun attachSurface(surface: Surface)

    fun detachSurface()

    fun destroy()
}

internal fun interface MpvClientFactory {
    fun create(applicationContext: Context): MpvClient?
}

internal enum class MpvClientFailure {
    EVENT_PUMP_STOPPED,
}

internal enum class MpvPropertyFormat(val rawValue: Int) {
    NONE(0),
    STRING(1),
    OSD_STRING(2),
    FLAG(3),
    INT64(4),
    DOUBLE(5),
    NODE(6),
    NODE_ARRAY(7),
    NODE_MAP(8),
    BYTE_ARRAY(9),
    UNKNOWN(Int.MIN_VALUE),
    ;

    companion object {
        fun fromRaw(rawValue: Int): MpvPropertyFormat =
            entries.firstOrNull { it != UNKNOWN && it.rawValue == rawValue } ?: UNKNOWN
    }
}

internal val MpvPropertyFormat.isPrimitiveObservationFormat: Boolean
    get() = when (this) {
        MpvPropertyFormat.NONE,
        MpvPropertyFormat.STRING,
        MpvPropertyFormat.OSD_STRING,
        MpvPropertyFormat.FLAG,
        MpvPropertyFormat.INT64,
        MpvPropertyFormat.DOUBLE,
        -> true

        MpvPropertyFormat.NODE,
        MpvPropertyFormat.NODE_ARRAY,
        MpvPropertyFormat.NODE_MAP,
        MpvPropertyFormat.BYTE_ARRAY,
        MpvPropertyFormat.UNKNOWN,
        -> false
    }

/** OSD_STRING is read-only in libmpv and cannot be observed. */
internal val MpvPropertyFormat.isSourceObservationFormat: Boolean
    get() = when (this) {
        MpvPropertyFormat.NONE,
        MpvPropertyFormat.STRING,
        MpvPropertyFormat.FLAG,
        MpvPropertyFormat.INT64,
        MpvPropertyFormat.DOUBLE,
        -> true

        MpvPropertyFormat.OSD_STRING,
        MpvPropertyFormat.NODE,
        MpvPropertyFormat.NODE_ARRAY,
        MpvPropertyFormat.NODE_MAP,
        MpvPropertyFormat.BYTE_ARRAY,
        MpvPropertyFormat.UNKNOWN,
        -> false
    }

internal sealed interface MpvPropertyValue {
    data object Unavailable : MpvPropertyValue

    data class Flag(val value: Boolean) : MpvPropertyValue

    data class Int64(val value: Long) : MpvPropertyValue

    data class DoubleValue(val value: Double) : MpvPropertyValue

    data class StringValue(val value: String) : MpvPropertyValue

    /** A payload format not exposed by the first source-client surface. */
    data class Unsupported(val rawFormat: Int) : MpvPropertyValue
}

internal data class MpvPropertyChange(
    val name: String,
    val rawFormat: Int,
    val value: MpvPropertyValue,
    val replyUserdata: Long? = null,
    val errorCode: Int = 0,
) {
    val format: MpvPropertyFormat = MpvPropertyFormat.fromRaw(rawFormat)
}

internal enum class MpvEventType(val rawValue: Int) {
    NONE(0),
    SHUTDOWN(1),
    LOG_MESSAGE(2),
    GET_PROPERTY_REPLY(3),
    SET_PROPERTY_REPLY(4),
    COMMAND_REPLY(5),
    START_FILE(6),
    END_FILE(7),
    FILE_LOADED(8),
    IDLE(11),
    TICK(14),
    CLIENT_MESSAGE(16),
    VIDEO_RECONFIG(17),
    AUDIO_RECONFIG(18),
    SEEK(20),
    PLAYBACK_RESTART(21),
    PROPERTY_CHANGE(22),
    QUEUE_OVERFLOW(24),
    HOOK(25),
    UNKNOWN(Int.MIN_VALUE),
    ;

    companion object {
        fun fromRaw(rawValue: Int): MpvEventType =
            entries.firstOrNull { it != UNKNOWN && it.rawValue == rawValue } ?: UNKNOWN
    }
}

internal enum class MpvEndFileReason(val rawValue: Int) {
    EOF(0),
    STOP(2),
    QUIT(3),
    ERROR(4),
    REDIRECT(5),
    UNKNOWN(Int.MIN_VALUE),
    ;

    companion object {
        fun fromRaw(rawValue: Int): MpvEndFileReason =
            entries.firstOrNull { it != UNKNOWN && it.rawValue == rawValue } ?: UNKNOWN
    }
}

internal data class MpvClientEvent(
    val rawEventId: Int,
    val errorCode: Int = 0,
    val replyUserdata: Long? = null,
    val playlistEntryId: Long? = null,
    val rawEndReason: Int? = null,
    val endErrorCode: Int = 0,
    val playlistInsertId: Long? = null,
    val playlistInsertCount: Int = 0,
) {
    val type: MpvEventType = MpvEventType.fromRaw(rawEventId)
    val endReason: MpvEndFileReason? = rawEndReason?.let(MpvEndFileReason::fromRaw)
}

internal class MpvOperationException(
    val operation: String,
    val errorCode: Int,
    detail: String? = null,
) : IllegalStateException(
    buildString {
        append(operation)
        append(" failed with libmpv error ")
        append(errorCode)
        if (!detail.isNullOrBlank()) {
            append(": ")
            append(detail)
        }
    },
)
