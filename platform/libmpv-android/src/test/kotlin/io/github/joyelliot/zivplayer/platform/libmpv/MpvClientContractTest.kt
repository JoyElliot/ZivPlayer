// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MpvClientContractTest {
    @Test
    fun propertyFormatsMatchTheLibmpvClientAbi() {
        assertEquals(
            listOf(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
            MpvPropertyFormat.entries
                .filterNot { it == MpvPropertyFormat.UNKNOWN }
                .map(MpvPropertyFormat::rawValue),
        )
        assertEquals(MpvPropertyFormat.UNKNOWN, MpvPropertyFormat.fromRaw(10_000))
        assertTrue(MpvPropertyFormat.DOUBLE.isPrimitiveObservationFormat)
        assertTrue(MpvPropertyFormat.OSD_STRING.isPrimitiveObservationFormat)
        assertFalse(MpvPropertyFormat.NODE.isPrimitiveObservationFormat)
        assertFalse(MpvPropertyFormat.BYTE_ARRAY.isPrimitiveObservationFormat)
        assertFalse(MpvPropertyFormat.UNKNOWN.isPrimitiveObservationFormat)
    }

    @Test
    fun eventIdsPreserveUnknownValues() {
        assertEquals(MpvEventType.START_FILE, MpvEventType.fromRaw(6))
        assertEquals(MpvEventType.END_FILE, MpvEventType.fromRaw(7))
        assertEquals(MpvEventType.QUEUE_OVERFLOW, MpvEventType.fromRaw(24))
        assertEquals(MpvEventType.UNKNOWN, MpvEventType.fromRaw(9_999))
    }

    @Test
    fun endReasonsMapKnownAndUnknownValues() {
        assertEquals(MpvEndFileReason.EOF, MpvEndFileReason.fromRaw(0))
        assertEquals(MpvEndFileReason.STOP, MpvEndFileReason.fromRaw(2))
        assertEquals(MpvEndFileReason.ERROR, MpvEndFileReason.fromRaw(4))
        assertEquals(MpvEndFileReason.UNKNOWN, MpvEndFileReason.fromRaw(1))
    }

    @Test
    fun eventPayloadRetainsUnknownRawValuesAndCorrelation() {
        val event = MpvClientEvent(
            rawEventId = 7,
            errorCode = -13,
            replyUserdata = 42L,
            playlistEntryId = 91L,
            rawEndReason = 99,
            endErrorCode = -13,
            playlistInsertId = 100L,
            playlistInsertCount = 2,
        )
        val change = MpvPropertyChange(
            name = "future-property",
            rawFormat = 77,
            value = MpvPropertyValue.Unsupported(77),
            replyUserdata = 43L,
            errorCode = -9,
        )

        assertEquals(99, event.rawEndReason)
        assertEquals(MpvEndFileReason.UNKNOWN, event.endReason)
        assertEquals(42L, event.replyUserdata)
        assertEquals(91L, event.playlistEntryId)
        assertEquals(100L, event.playlistInsertId)
        assertEquals(2, event.playlistInsertCount)
        assertEquals(77, change.rawFormat)
        assertEquals(MpvPropertyFormat.UNKNOWN, change.format)
        assertEquals(43L, change.replyUserdata)
        assertEquals(MpvPropertyValue.Unsupported(77), change.value)
    }

    @Test
    fun operationFailureKeepsCodeAndSanitizedContext() {
        val failure = MpvOperationException(
            operation = "set property 'pause'",
            errorCode = -10,
            detail = "property unavailable",
        )

        assertEquals("set property 'pause'", failure.operation)
        assertEquals(-10, failure.errorCode)
        assertEquals(
            "set property 'pause' failed with libmpv error -10: property unavailable",
            failure.message,
        )
    }
}
