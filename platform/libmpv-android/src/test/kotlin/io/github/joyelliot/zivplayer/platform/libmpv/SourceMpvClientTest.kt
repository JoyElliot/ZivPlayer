// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.content.ContextWrapper
import android.view.Surface
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SourceMpvClientTest {
    @Test
    fun zeroNativeTokenReturnsNoClient() {
        val nativeApi = FakeNativeApi(createToken = 0L)

        val client = SourceMpvClient.create(
            applicationContext = TestContext,
            nativeApi = nativeApi,
            failureLogger = IgnoreFailures,
        )

        assertNull(client)
        assertEquals(1, nativeApi.createCalls.get())
    }

    @Test
    fun initializationFailureDoesNotStartEventPumpAndRemainsDestroyable() {
        val nativeApi = FakeNativeApi(initializeStatus = -3)
        val threadFactory = RecordingThreadFactory()
        val client = createClient(nativeApi, eventThreadFactory = threadFactory)

        val failure = assertThrows(MpvOperationException::class.java) {
            client.initialize()
        }

        assertEquals("initialize", failure.operation)
        assertEquals(-3, failure.errorCode)
        assertEquals(0, threadFactory.createCalls.get())
        assertThrows(IllegalStateException::class.java) {
            client.command(arrayOf("stop"))
        }
        client.destroy()
        client.destroy()
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun initializationExceptionBreaksClientAndDoesNotRetryAnIndeterminateHandle() {
        val expected = IllegalStateException("expected native initialize exception")
        val nativeApi = FakeNativeApi(initializeFailure = expected)
        val client = createClient(nativeApi)

        assertSame(expected, runCatching { client.initialize() }.exceptionOrNull())
        assertThrows(IllegalStateException::class.java) {
            client.initialize()
        }
        assertEquals(1, nativeApi.initializeCalls.get())

        client.destroy()
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun initializeStartsOnePumpAndRejectsDuplicateInitialization() {
        val nativeApi = FakeNativeApi()
        val threadFactory = RecordingThreadFactory()
        val client = createClient(nativeApi, eventThreadFactory = threadFactory)

        client.initialize()
        assertTrue(nativeApi.waitEntered.await(1, TimeUnit.SECONDS))

        assertThrows(IllegalStateException::class.java) {
            client.initialize()
        }
        assertEquals(1, nativeApi.initializeCalls.get())
        assertEquals(1, threadFactory.createCalls.get())
        client.destroy()
    }

    @Test
    fun gettersUseTypedOutputAndOnlyUnavailableReturnsNull() {
        val nativeApi = FakeNativeApi()
        val client = initializedClient(nativeApi)
        try {
            nativeApi.doubleValue = 42.5
            nativeApi.doubleStatus = 0
            assertEquals(42.5, client.getPropertyDouble("duration")!!, 0.0)
            assertEquals(1, nativeApi.lastDoubleOutputSize)

            nativeApi.doubleValue = 99.0
            nativeApi.doubleStatus = MPV_ERROR_PROPERTY_UNAVAILABLE
            assertNull(client.getPropertyDouble("duration"))

            nativeApi.doubleStatus = -8
            val doubleFailure = assertThrows(MpvOperationException::class.java) {
                client.getPropertyDouble("missing")
            }
            assertEquals(-8, doubleFailure.errorCode)
            assertEquals("get property 'missing'", doubleFailure.operation)

            nativeApi.booleanValue = 1
            nativeApi.booleanStatus = 0
            assertEquals(true, client.getPropertyBoolean("pause"))
            assertEquals(1, nativeApi.lastBooleanOutputSize)

            nativeApi.booleanValue = 0
            assertEquals(false, client.getPropertyBoolean("pause"))

            nativeApi.booleanValue = 1
            nativeApi.booleanStatus = MPV_ERROR_PROPERTY_UNAVAILABLE
            assertNull(client.getPropertyBoolean("pause"))

            nativeApi.booleanStatus = -9
            val booleanFailure = assertThrows(MpvOperationException::class.java) {
                client.getPropertyBoolean("missing-flag")
            }
            assertEquals(-9, booleanFailure.errorCode)
            assertEquals("get property 'missing-flag'", booleanFailure.operation)
        } finally {
            client.destroy()
        }
    }

    @Test
    fun everySynchronousOperationPropagatesItsNativeStatus() {
        val optionApi = FakeNativeApi().apply { setOptionStatus = -4 }
        val optionClient = createClient(optionApi)
        val optionFailure = assertThrows(MpvOperationException::class.java) {
            optionClient.setOptionString("config", "no")
        }
        assertEquals(-4, optionFailure.errorCode)
        optionClient.destroy()

        val nativeApi = FakeNativeApi()
        val client = initializedClient(nativeApi)
        fun expectFailure(code: Int, operation: () -> Unit) {
            assertEquals(code, assertThrows(MpvOperationException::class.java, operation).errorCode)
        }
        try {
            nativeApi.commandStatus = -11
            expectFailure(-11) { client.command(arrayOf("stop")) }
            nativeApi.setPropertyDoubleStatus = -12
            expectFailure(-12) { client.setPropertyDouble("volume", 50.0) }
            nativeApi.setPropertyBooleanStatus = -13
            expectFailure(-13) { client.setPropertyBoolean("pause", true) }
            nativeApi.setPropertyStringStatus = -14
            expectFailure(-14) { client.setPropertyString("aid", "no") }
            nativeApi.observeStatus = -15
            expectFailure(-15) {
                client.observeProperty("time-pos", MpvPropertyFormat.DOUBLE, 1L)
            }
            nativeApi.unobserveStatus = -16
            expectFailure(-16) { client.unobserveProperty(1L) }
            nativeApi.unobserveStatus = 2
            assertEquals(2, client.unobserveProperty(1L))
        } finally {
            client.destroy()
        }
    }

    @Test
    fun propertyEventsPreserveTypedAndUnknownPayloadsDespiteObserverFailure() {
        val nativeApi = FakeNativeApi()
        val client = createClient(nativeApi)
        val changes = Collections.synchronizedList(mutableListOf<MpvPropertyChange>())
        val delivered = CountDownLatch(3)
        client.addObserver(
            TestObserver(
                onProperty = { error("expected observer failure") },
            ),
        )
        client.addObserver(
            TestObserver(
                onProperty = {
                    changes += it
                    delivered.countDown()
                },
            ),
        )
        client.initialize()
        try {
            nativeApi.events.put(
                propertyEvent(
                    rawFormat = MpvPropertyFormat.DOUBLE.rawValue,
                    value = MpvNativeProperty(
                        name = "time-pos",
                        rawFormat = MpvPropertyFormat.DOUBLE.rawValue,
                        hasValue = true,
                        doubleValue = 12.25,
                    ),
                    replyUserdata = 11L,
                ),
            )
            nativeApi.events.put(
                propertyEvent(
                    rawFormat = MpvPropertyFormat.NONE.rawValue,
                    value = MpvNativeProperty(
                        name = "duration",
                        rawFormat = MpvPropertyFormat.NONE.rawValue,
                        hasValue = false,
                    ),
                    errorCode = MPV_ERROR_PROPERTY_UNAVAILABLE,
                ),
            )
            nativeApi.events.put(
                propertyEvent(
                    rawFormat = 77,
                    value = MpvNativeProperty(
                        name = "future-property",
                        rawFormat = 77,
                        hasValue = false,
                    ),
                    replyUserdata = -1L,
                ),
            )

            assertTrue(delivered.await(1, TimeUnit.SECONDS))
            assertEquals(MpvPropertyValue.DoubleValue(12.25), changes[0].value)
            assertEquals(11L, changes[0].replyUserdata)
            assertEquals(MpvPropertyValue.Unavailable, changes[1].value)
            assertEquals(MPV_ERROR_PROPERTY_UNAVAILABLE, changes[1].errorCode)
            assertEquals(MpvPropertyValue.Unsupported(77), changes[2].value)
            assertEquals(77, changes[2].rawFormat)
            assertEquals(-1L, changes[2].replyUserdata)
        } finally {
            client.destroy()
        }
    }

    @Test
    fun eventCallbackCanReenterGetterAndRetainsEndFileFields() {
        val nativeApi = FakeNativeApi().apply {
            doubleValue = 7.5
        }
        val client = createClient(nativeApi)
        val actual = AtomicReference<MpvClientEvent>()
        val getterValue = AtomicReference<Double>()
        val delivered = CountDownLatch(1)
        client.addObserver(
            TestObserver(
                onEvent = {
                    getterValue.set(client.getPropertyDouble("duration"))
                    actual.set(it)
                    delivered.countDown()
                },
            ),
        )
        client.initialize()
        try {
            nativeApi.events.put(
                MpvNativeEvent(
                    rawEventId = MpvEventType.END_FILE.rawValue,
                    errorCode = -13,
                    replyUserdata = -1L,
                    playlistEntryId = 0L,
                    rawEndReason = 99,
                    endErrorCode = -13,
                    playlistInsertId = 52L,
                    playlistInsertCount = 2,
                ),
            )

            assertTrue(delivered.await(1, TimeUnit.SECONDS))
            assertEquals(7.5, getterValue.get()!!, 0.0)
            assertEquals(MpvEndFileReason.UNKNOWN, actual.get().endReason)
            assertEquals(-1L, actual.get().replyUserdata)
            assertEquals(0L, actual.get().playlistEntryId)
            assertEquals(52L, actual.get().playlistInsertId)
            assertEquals(2, actual.get().playlistInsertCount)
        } finally {
            client.destroy()
        }
    }

    @Test
    fun sourceClientRejectsOsdAndStructuredObservationFormats() {
        val nativeApi = FakeNativeApi()
        val client = initializedClient(nativeApi)
        try {
            assertThrows(IllegalArgumentException::class.java) {
                client.observeProperty("media-title", MpvPropertyFormat.OSD_STRING, 1L)
            }
            assertThrows(IllegalArgumentException::class.java) {
                client.observeProperty("metadata", MpvPropertyFormat.NODE, 2L)
            }
            assertEquals(0, nativeApi.observeCalls.get())
        } finally {
            client.destroy()
        }
    }

    @Test
    fun eventPumpFailureIsReportedOnceAndMakesClientUnusable() {
        val expected = IllegalStateException("expected wait failure")
        val nativeApi = FakeNativeApi(waitFailure = expected)
        val client = createClient(nativeApi)
        val failures = Collections.synchronizedList(mutableListOf<MpvClientFailure>())
        val delivered = CountDownLatch(1)
        client.addObserver(
            TestObserver(
                onFailure = {
                    failures += it
                    delivered.countDown()
                },
            ),
        )

        client.initialize()

        assertTrue(delivered.await(1, TimeUnit.SECONDS))
        assertEquals(listOf(MpvClientFailure.EVENT_PUMP_STOPPED), failures)
        assertThrows(IllegalStateException::class.java) {
            client.command(arrayOf("stop"))
        }
        assertThrows(IllegalStateException::class.java) {
            client.addObserver(TestObserver())
        }
        client.destroy()
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun malformedRequiredEventPayloadsFailClosedBeforeAnyEventCallback() {
        val malformed = listOf(
            MpvNativeEvent(rawEventId = MpvEventType.START_FILE.rawValue),
            MpvNativeEvent(
                rawEventId = MpvEventType.END_FILE.rawValue,
                rawEndReason = 0,
            ),
            MpvNativeEvent(
                rawEventId = MpvEventType.END_FILE.rawValue,
                playlistEntryId = 0L,
            ),
            MpvNativeEvent(rawEventId = MpvEventType.PROPERTY_CHANGE.rawValue),
        )

        malformed.forEachIndexed { index, malformedEvent ->
            val nativeApi = FakeNativeApi()
            val client = createClient(nativeApi)
            val events = AtomicInteger()
            val failures = Collections.synchronizedList(mutableListOf<MpvClientFailure>())
            val delivered = CountDownLatch(1)
            client.addObserver(
                TestObserver(
                    onEvent = { events.incrementAndGet() },
                    onProperty = { events.incrementAndGet() },
                    onFailure = {
                        failures += it
                        delivered.countDown()
                    },
                ),
            )
            client.initialize()
            nativeApi.events.put(malformedEvent)

            assertTrue("malformed case $index did not fail", delivered.await(1, TimeUnit.SECONDS))
            assertEquals(listOf(MpvClientFailure.EVENT_PUMP_STOPPED), failures)
            assertEquals(0, events.get())
            client.destroy()
        }
    }

    @Test
    fun shutdownIsDeliveredBeforeThePumpReportsItsTerminalFailure() {
        val nativeApi = FakeNativeApi()
        val client = createClient(nativeApi)
        val callbacks = Collections.synchronizedList(mutableListOf<String>())
        val delivered = CountDownLatch(2)
        client.addObserver(
            TestObserver(
                onEvent = {
                    callbacks += "event:${it.type.name}"
                    delivered.countDown()
                },
                onFailure = {
                    callbacks += "failure:${it.name}"
                    delivered.countDown()
                },
            ),
        )
        client.initialize()
        nativeApi.events.put(MpvNativeEvent(MpvEventType.SHUTDOWN.rawValue))

        assertTrue(delivered.await(1, TimeUnit.SECONDS))
        assertEquals(
            listOf("event:SHUTDOWN", "failure:EVENT_PUMP_STOPPED"),
            callbacks,
        )
        client.destroy()
    }

    @Test
    fun closeStopsACopyOnWriteDispatchBeforeTheNextObserver() {
        val nativeApi = FakeNativeApi()
        val client = createClient(nativeApi)
        val firstEntered = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val secondCalls = AtomicInteger()
        client.addObserver(
            TestObserver(
                onEvent = {
                    firstEntered.countDown()
                    releaseFirst.await()
                },
            ),
        )
        client.addObserver(TestObserver(onEvent = { secondCalls.incrementAndGet() }))
        client.initialize()
        nativeApi.events.put(MpvNativeEvent(MpvEventType.FILE_LOADED.rawValue))
        assertTrue(firstEntered.await(1, TimeUnit.SECONDS))

        val destroyFailure = AtomicReference<Throwable?>()
        val destroyer = Thread {
            destroyFailure.set(runCatching { client.destroy() }.exceptionOrNull())
        }
        destroyer.start()
        assertTrue(nativeApi.wakeupEntered.await(1, TimeUnit.SECONDS))
        releaseFirst.countDown()
        destroyer.join(1_000)

        assertFalse(destroyer.isAlive)
        assertNull(destroyFailure.get())
        assertEquals(0, secondCalls.get())
    }

    @Test
    fun mainThreadDestroyIsRejectedBeforeStateMutation() {
        val nativeApi = FakeNativeApi()
        val callingThread = Thread.currentThread()
        val client = initializedClient(
            nativeApi = nativeApi,
            isMainThread = { Thread.currentThread() === callingThread },
        )

        assertThrows(IllegalStateException::class.java) {
            client.destroy()
        }
        client.command(arrayOf("stop"))
        assertEquals(0, nativeApi.wakeupCalls.get())
        assertEquals(0, nativeApi.terminateCalls.get())

        val teardownFailure = AtomicReference<Throwable?>()
        val teardown = Thread {
            teardownFailure.set(runCatching { client.destroy() }.exceptionOrNull())
        }
        teardown.start()
        teardown.join(1_000)
        assertFalse(teardown.isAlive)
        assertNull(teardownFailure.get())
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun eventThreadCannotDestroyItself() {
        val nativeApi = FakeNativeApi()
        val client = createClient(nativeApi)
        val selfDestroyFailure = AtomicReference<Throwable?>()
        val callbackFinished = CountDownLatch(1)
        client.addObserver(
            TestObserver(
                onEvent = {
                    selfDestroyFailure.set(runCatching { client.destroy() }.exceptionOrNull())
                    callbackFinished.countDown()
                },
            ),
        )
        client.initialize()
        nativeApi.events.put(MpvNativeEvent(MpvEventType.FILE_LOADED.rawValue))

        assertTrue(callbackFinished.await(1, TimeUnit.SECONDS))
        assertTrue(selfDestroyFailure.get() is IllegalStateException)
        assertEquals(0, nativeApi.wakeupCalls.get())
        assertEquals(0, nativeApi.terminateCalls.get())
        client.destroy()
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun concurrentDestroyersShareOneNativeTeardown() {
        val nativeApi = FakeNativeApi(blockTerminateUntilReleased = true)
        val client = initializedClient(nativeApi)
        val start = CountDownLatch(1)
        val entered = CountDownLatch(2)
        val failures = Collections.synchronizedList(mutableListOf<Throwable>())
        val destroyers = List(2) {
            Thread {
                start.await()
                entered.countDown()
                runCatching { client.destroy() }.exceptionOrNull()?.let(failures::add)
            }
        }

        destroyers.forEach(Thread::start)
        start.countDown()
        assertTrue(entered.await(1, TimeUnit.SECONDS))
        assertTrue(nativeApi.terminateEntered.await(1, TimeUnit.SECONDS))
        assertEquals(1, nativeApi.terminateCalls.get())
        assertTrue(destroyers.any(Thread::isAlive))
        nativeApi.releaseTerminate.countDown()
        destroyers.forEach { it.join(1_000) }

        assertTrue(destroyers.none(Thread::isAlive))
        assertTrue(failures.isEmpty())
        assertEquals(1, nativeApi.wakeupCalls.get())
        assertEquals(1, nativeApi.terminateCalls.get())
        client.destroy()
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun joinTimeoutDoesNotTerminateAndCanBeRetriedAfterPumpStops() {
        val nativeApi = FakeNativeApi(blockWaitUntilReleased = true)
        val client = initializedClient(
            nativeApi = nativeApi,
            eventThreadJoinTimeoutMillis = 25L,
        )

        assertThrows(IllegalStateException::class.java) {
            client.destroy()
        }
        assertEquals(0, nativeApi.terminateCalls.get())

        nativeApi.releaseBlockedWait.countDown()
        assertTrue(nativeApi.waitReturned.await(1, TimeUnit.SECONDS))
        client.destroy()
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun unknownNativeDestroyCommitIsNeverRetried() {
        val terminalFailure = IllegalStateException("expected terminal destroy failure")
        val nativeApi = FakeNativeApi(terminateFailure = terminalFailure)
        val client = initializedClient(nativeApi)

        assertSame(
            terminalFailure,
            runCatching { client.destroy() }.exceptionOrNull(),
        )
        assertSame(
            terminalFailure,
            runCatching { client.destroy() }.exceptionOrNull(),
        )
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun negativeNativeDestroyStatusIsPreCommitAndCanBeRetried() {
        val nativeApi = FakeNativeApi().apply { terminateStatus = -24 }
        val client = initializedClient(nativeApi)

        val failure = assertThrows(MpvOperationException::class.java) {
            client.destroy()
        }
        assertEquals("terminate native client", failure.operation)
        assertEquals(-24, failure.errorCode)
        assertEquals(1, nativeApi.terminateCalls.get())

        nativeApi.terminateStatus = 0
        client.destroy()
        client.destroy()
        assertEquals(2, nativeApi.terminateCalls.get())
    }

    @Test
    fun wakeupFailureDoesNotTerminateAndCanBeRetried() {
        val nativeApi = FakeNativeApi().apply { wakeupStatus = -25 }
        val client = initializedClient(nativeApi)

        val failure = assertThrows(MpvOperationException::class.java) {
            client.destroy()
        }
        assertEquals("wake event thread", failure.operation)
        assertEquals(-25, failure.errorCode)
        assertEquals(0, nativeApi.terminateCalls.get())

        nativeApi.wakeupStatus = 0
        client.destroy()
        assertEquals(2, nativeApi.wakeupCalls.get())
        assertEquals(1, nativeApi.terminateCalls.get())
    }

    @Test
    fun surfaceStatusAndDestroyOrderingRemainRetryableBeforeNativeTermination() {
        val nativeApi = FakeNativeApi()
        val client = initializedClient(nativeApi)
        val surface = allocateSurfaceWithoutCallingAndroidStub()

        nativeApi.attachStatus = -20
        assertEquals(
            -20,
            assertThrows(MpvOperationException::class.java) {
                client.attachSurface(surface)
            }.errorCode,
        )
        nativeApi.attachStatus = 0
        client.attachSurface(surface)
        nativeApi.detachStatus = -21
        assertEquals(
            -21,
            assertThrows(MpvOperationException::class.java) {
                client.detachSurface()
            }.errorCode,
        )
        nativeApi.detachStatus = 0
        client.destroy()

        assertEquals(2, nativeApi.attachCalls.get())
        assertEquals(2, nativeApi.detachCalls.get())
        assertEquals(
            listOf("attach", "attach", "detach", "wakeup", "detach", "terminate"),
            nativeApi.lifecycleCalls,
        )
    }

    @Test
    fun defensiveDestroyDetachFailureCanBeRetriedBeforeTermination() {
        val nativeApi = FakeNativeApi()
        val client = initializedClient(nativeApi)
        client.attachSurface(allocateSurfaceWithoutCallingAndroidStub())
        nativeApi.detachStatus = -21

        val failure = assertThrows(MpvOperationException::class.java) {
            client.destroy()
        }
        assertEquals("detach Surface", failure.operation)
        assertEquals(-21, failure.errorCode)
        assertEquals(0, nativeApi.terminateCalls.get())

        nativeApi.detachStatus = 0
        client.destroy()
        assertEquals(2, nativeApi.detachCalls.get())
        assertEquals(1, nativeApi.terminateCalls.get())
        assertEquals(
            listOf("attach", "wakeup", "detach", "detach", "terminate"),
            nativeApi.lifecycleCalls,
        )
    }

    @Test
    fun nativeEventBufferPreservesPresenceAndRawWidths() {
        val integers = IntArray(MpvNativeBindings.INTEGER_FIELD_COUNT).apply {
            this[MpvNativeBindings.INTEGER_EVENT_ID] = MpvEventType.END_FILE.rawValue
            this[MpvNativeBindings.INTEGER_EVENT_ERROR] = -13
            this[MpvNativeBindings.INTEGER_END_REASON] = 99
            this[MpvNativeBindings.INTEGER_END_ERROR] = -13
            this[MpvNativeBindings.INTEGER_INSERT_COUNT] = 2
            this[MpvNativeBindings.INTEGER_PRESENCE_FLAGS] =
                MpvNativeBindings.PRESENCE_REPLY_USERDATA or
                MpvNativeBindings.PRESENCE_PLAYLIST_ENTRY_ID or
                MpvNativeBindings.PRESENCE_END_REASON or
                MpvNativeBindings.PRESENCE_PLAYLIST_INSERT_ID
        }
        val longs = LongArray(MpvNativeBindings.LONG_FIELD_COUNT).apply {
            this[MpvNativeBindings.LONG_REPLY_USERDATA] = -1L
            this[MpvNativeBindings.LONG_PLAYLIST_ENTRY_ID] = 0L
            this[MpvNativeBindings.LONG_PLAYLIST_INSERT_ID] = Long.MAX_VALUE
        }

        val event = MpvNativeBindings.decodeEvent(
            integers = integers,
            longs = longs,
            doubles = DoubleArray(MpvNativeBindings.DOUBLE_FIELD_COUNT),
            strings = arrayOfNulls(MpvNativeBindings.STRING_FIELD_COUNT),
        )

        assertEquals(-1L, event.replyUserdata)
        assertEquals(0L, event.playlistEntryId)
        assertEquals(99, event.rawEndReason)
        assertEquals(Long.MAX_VALUE, event.playlistInsertId)
        assertEquals(2, event.playlistInsertCount)
        assertEquals(-13, event.errorCode)
        assertEquals(-13, event.endErrorCode)

        longs[MpvNativeBindings.LONG_REPLY_USERDATA] = 0L
        val zeroReply = MpvNativeBindings.decodeEvent(
            integers = integers,
            longs = longs,
            doubles = DoubleArray(MpvNativeBindings.DOUBLE_FIELD_COUNT),
            strings = arrayOfNulls(MpvNativeBindings.STRING_FIELD_COUNT),
        )
        assertEquals(0L, zeroReply.replyUserdata)

        integers[MpvNativeBindings.INTEGER_PRESENCE_FLAGS] = 0
        longs[MpvNativeBindings.LONG_REPLY_USERDATA] = 71L
        longs[MpvNativeBindings.LONG_PLAYLIST_ENTRY_ID] = 72L
        longs[MpvNativeBindings.LONG_PLAYLIST_INSERT_ID] = 73L
        integers[MpvNativeBindings.INTEGER_END_REASON] = 74
        val absent = MpvNativeBindings.decodeEvent(
            integers = integers,
            longs = longs,
            doubles = DoubleArray(MpvNativeBindings.DOUBLE_FIELD_COUNT),
            strings = arrayOfNulls(MpvNativeBindings.STRING_FIELD_COUNT),
        )
        assertNull(absent.replyUserdata)
        assertNull(absent.playlistEntryId)
        assertNull(absent.rawEndReason)
        assertNull(absent.playlistInsertId)
    }

    @Test
    fun sourceEventsConvertEveryPrimitivePropertyValueWithoutNarrowing() {
        val nativeApi = FakeNativeApi()
        val client = createClient(nativeApi)
        val values = Collections.synchronizedList(mutableListOf<MpvPropertyValue>())
        val delivered = CountDownLatch(6)
        client.addObserver(
            TestObserver(
                onProperty = {
                    values += it.value
                    delivered.countDown()
                },
            ),
        )
        client.initialize()
        val properties = listOf(
            MpvNativeProperty("flag-true", MpvPropertyFormat.FLAG.rawValue, true, flagValue = true),
            MpvNativeProperty("flag-false", MpvPropertyFormat.FLAG.rawValue, true, flagValue = false),
            MpvNativeProperty("int-min", MpvPropertyFormat.INT64.rawValue, true, int64Value = Long.MIN_VALUE),
            MpvNativeProperty("int-max", MpvPropertyFormat.INT64.rawValue, true, int64Value = Long.MAX_VALUE),
            MpvNativeProperty("empty", MpvPropertyFormat.STRING.rawValue, true, stringValue = ""),
            MpvNativeProperty("null", MpvPropertyFormat.STRING.rawValue, true, stringValue = null),
        )
        properties.forEach { property ->
            nativeApi.events.put(
                propertyEvent(rawFormat = property.rawFormat, value = property),
            )
        }

        assertTrue(delivered.await(1, TimeUnit.SECONDS))
        assertEquals(
            listOf(
                MpvPropertyValue.Flag(true),
                MpvPropertyValue.Flag(false),
                MpvPropertyValue.Int64(Long.MIN_VALUE),
                MpvPropertyValue.Int64(Long.MAX_VALUE),
                MpvPropertyValue.StringValue(""),
                MpvPropertyValue.Unavailable,
            ),
            values,
        )
        client.destroy()
    }

    @Test
    fun nativeEventBufferDecodesEverySupportedPrimitivePropertyFormat() {
        fun decode(
            format: MpvPropertyFormat,
            flag: Int = 0,
            int64: Long = 0L,
            double: Double = 0.0,
            string: String? = null,
            hasValue: Boolean = true,
        ): MpvNativeProperty {
            val integers = IntArray(MpvNativeBindings.INTEGER_FIELD_COUNT).apply {
                this[MpvNativeBindings.INTEGER_EVENT_ID] = MpvEventType.PROPERTY_CHANGE.rawValue
                this[MpvNativeBindings.INTEGER_PROPERTY_FORMAT] = format.rawValue
                this[MpvNativeBindings.INTEGER_PROPERTY_FLAG] = flag
                if (hasValue) {
                    this[MpvNativeBindings.INTEGER_PRESENCE_FLAGS] =
                        MpvNativeBindings.PRESENCE_PROPERTY_VALUE
                }
            }
            val longs = LongArray(MpvNativeBindings.LONG_FIELD_COUNT).apply {
                this[MpvNativeBindings.LONG_PROPERTY_INT64] = int64
            }
            val doubles = DoubleArray(MpvNativeBindings.DOUBLE_FIELD_COUNT).apply {
                this[MpvNativeBindings.DOUBLE_PROPERTY_VALUE] = double
            }
            val strings = arrayOfNulls<String>(MpvNativeBindings.STRING_FIELD_COUNT).apply {
                this[MpvNativeBindings.STRING_PROPERTY_NAME] = "property"
                this[MpvNativeBindings.STRING_PROPERTY_VALUE] = string
            }
            return requireNotNull(
                MpvNativeBindings.decodeEvent(integers, longs, doubles, strings).property,
            )
        }

        assertEquals(true, decode(MpvPropertyFormat.FLAG, flag = 1).flagValue)
        assertEquals(-1L, decode(MpvPropertyFormat.INT64, int64 = -1L).int64Value)
        assertEquals(2.5, decode(MpvPropertyFormat.DOUBLE, double = 2.5).doubleValue, 0.0)
        assertEquals("value", decode(MpvPropertyFormat.STRING, string = "value").stringValue)
        assertFalse(decode(MpvPropertyFormat.NONE, hasValue = false).hasValue)
    }

    @Test
    fun malformedNativeEventBuffersAreRejected() {
        val integers = IntArray(MpvNativeBindings.INTEGER_FIELD_COUNT)
        val longs = LongArray(MpvNativeBindings.LONG_FIELD_COUNT)
        val doubles = DoubleArray(MpvNativeBindings.DOUBLE_FIELD_COUNT)
        val strings = arrayOfNulls<String>(MpvNativeBindings.STRING_FIELD_COUNT)

        assertThrows(IllegalArgumentException::class.java) {
            MpvNativeBindings.decodeEvent(IntArray(integers.size - 1), longs, doubles, strings)
        }
        assertThrows(IllegalArgumentException::class.java) {
            MpvNativeBindings.decodeEvent(integers, LongArray(longs.size - 1), doubles, strings)
        }
        assertThrows(IllegalArgumentException::class.java) {
            MpvNativeBindings.decodeEvent(integers, longs, DoubleArray(0), strings)
        }
        assertThrows(IllegalArgumentException::class.java) {
            MpvNativeBindings.decodeEvent(integers, longs, doubles, arrayOfNulls(1))
        }
    }

    private fun initializedClient(
        nativeApi: FakeNativeApi,
        isMainThread: () -> Boolean = { false },
        eventThreadJoinTimeoutMillis: Long = 1_000L,
    ): SourceMpvClient = createClient(
        nativeApi = nativeApi,
        isMainThread = isMainThread,
        eventThreadJoinTimeoutMillis = eventThreadJoinTimeoutMillis,
    ).also { client ->
        client.initialize()
        assertTrue(nativeApi.waitEntered.await(1, TimeUnit.SECONDS))
    }

    private fun createClient(
        nativeApi: FakeNativeApi,
        eventThreadFactory: MpvEventThreadFactory = RecordingThreadFactory(),
        isMainThread: () -> Boolean = { false },
        eventThreadJoinTimeoutMillis: Long = 1_000L,
    ): SourceMpvClient = checkNotNull(
        SourceMpvClient.create(
            applicationContext = TestContext,
            nativeApi = nativeApi,
            eventThreadFactory = eventThreadFactory,
            isMainThread = isMainThread,
            eventThreadJoinTimeoutMillis = eventThreadJoinTimeoutMillis,
            failureLogger = IgnoreFailures,
        ),
    )

    private fun propertyEvent(
        rawFormat: Int,
        value: MpvNativeProperty,
        replyUserdata: Long? = null,
        errorCode: Int = 0,
    ) = MpvNativeEvent(
        rawEventId = MpvEventType.PROPERTY_CHANGE.rawValue,
        errorCode = errorCode,
        replyUserdata = replyUserdata,
        property = value.copy(rawFormat = rawFormat),
    )

    private class RecordingThreadFactory : MpvEventThreadFactory {
        val createCalls = AtomicInteger()

        override fun create(token: Long, eventPump: () -> Unit): Thread {
            createCalls.incrementAndGet()
            return Thread(eventPump, "test-mpv-event-$token").apply {
                isDaemon = true
            }
        }
    }

    private class TestObserver(
        private val onProperty: (MpvPropertyChange) -> Unit = {},
        private val onEvent: (MpvClientEvent) -> Unit = {},
        private val onFailure: (MpvClientFailure) -> Unit = {},
    ) : MpvClient.Observer {
        override fun onPropertyChanged(change: MpvPropertyChange) = onProperty(change)

        override fun onEvent(event: MpvClientEvent) = onEvent.invoke(event)

        override fun onFailure(failure: MpvClientFailure) = onFailure.invoke(failure)
    }

    private class FakeNativeApi(
        private val createToken: Long = 41L,
        var initializeStatus: Int = 0,
        private val initializeFailure: Throwable? = null,
        private val blockWaitUntilReleased: Boolean = false,
        private val waitFailure: Throwable? = null,
        private val blockTerminateUntilReleased: Boolean = false,
        private val terminateFailure: Throwable? = null,
    ) : MpvNativeApi {
        val events = LinkedBlockingQueue<MpvNativeEvent>()
        val waitEntered = CountDownLatch(1)
        val waitReturned = CountDownLatch(1)
        val releaseBlockedWait = CountDownLatch(1)
        val terminateEntered = CountDownLatch(1)
        val releaseTerminate = CountDownLatch(1)
        val wakeupEntered = CountDownLatch(1)
        val createCalls = AtomicInteger()
        val initializeCalls = AtomicInteger()
        val observeCalls = AtomicInteger()
        val wakeupCalls = AtomicInteger()
        val attachCalls = AtomicInteger()
        val detachCalls = AtomicInteger()
        val terminateCalls = AtomicInteger()
        val lifecycleCalls = Collections.synchronizedList(mutableListOf<String>())

        @Volatile
        var doubleStatus: Int = 0

        @Volatile
        var setOptionStatus: Int = 0

        @Volatile
        var commandStatus: Int = 0

        @Volatile
        var setPropertyDoubleStatus: Int = 0

        @Volatile
        var setPropertyBooleanStatus: Int = 0

        @Volatile
        var setPropertyStringStatus: Int = 0

        @Volatile
        var observeStatus: Int = 0

        @Volatile
        var unobserveStatus: Int = 0

        @Volatile
        var wakeupStatus: Int = 0

        @Volatile
        var attachStatus: Int = 0

        @Volatile
        var detachStatus: Int = 0

        @Volatile
        var terminateStatus: Int = 0

        @Volatile
        var doubleValue: Double = 0.0

        @Volatile
        var booleanStatus: Int = 0

        @Volatile
        var booleanValue: Int = 0

        @Volatile
        var lastDoubleOutputSize: Int = 0

        @Volatile
        var lastBooleanOutputSize: Int = 0

        override fun create(applicationContext: Context): Long {
            createCalls.incrementAndGet()
            return createToken
        }

        override fun setOptionString(token: Long, name: String, value: String): Int =
            setOptionStatus

        override fun initialize(token: Long): Int {
            initializeCalls.incrementAndGet()
            initializeFailure?.let { throw it }
            return initializeStatus
        }

        override fun command(token: Long, arguments: Array<String>): Int = commandStatus

        override fun getPropertyDouble(token: Long, name: String, output: DoubleArray): Int {
            lastDoubleOutputSize = output.size
            output[0] = doubleValue
            return doubleStatus
        }

        override fun getPropertyBoolean(token: Long, name: String, output: IntArray): Int {
            lastBooleanOutputSize = output.size
            output[0] = booleanValue
            return booleanStatus
        }

        override fun setPropertyDouble(token: Long, name: String, value: Double): Int =
            setPropertyDoubleStatus

        override fun setPropertyBoolean(token: Long, name: String, value: Boolean): Int =
            setPropertyBooleanStatus

        override fun setPropertyString(token: Long, name: String, value: String): Int =
            setPropertyStringStatus

        override fun observeProperty(
            token: Long,
            name: String,
            rawFormat: Int,
            replyUserdata: Long,
        ): Int {
            observeCalls.incrementAndGet()
            return observeStatus
        }

        override fun unobserveProperty(token: Long, replyUserdata: Long): Int = unobserveStatus

        override fun waitEvent(token: Long, timeoutSeconds: Double): MpvNativeEvent {
            waitEntered.countDown()
            waitFailure?.let { throw it }
            val event = if (blockWaitUntilReleased) {
                releaseBlockedWait.await()
                MpvNativeEvent(MpvEventType.NONE.rawValue)
            } else {
                events.take()
            }
            waitReturned.countDown()
            return event
        }

        override fun wakeup(token: Long): Int {
            wakeupCalls.incrementAndGet()
            lifecycleCalls += "wakeup"
            wakeupEntered.countDown()
            if (wakeupStatus >= 0 && !blockWaitUntilReleased) {
                events.offer(MpvNativeEvent(MpvEventType.NONE.rawValue))
            }
            return wakeupStatus
        }

        override fun attachSurface(token: Long, surface: Surface): Int {
            attachCalls.incrementAndGet()
            lifecycleCalls += "attach"
            return attachStatus
        }

        override fun detachSurface(token: Long): Int {
            detachCalls.incrementAndGet()
            lifecycleCalls += "detach"
            return detachStatus
        }

        override fun terminateDestroy(token: Long): Int {
            terminateCalls.incrementAndGet()
            lifecycleCalls += "terminate"
            terminateEntered.countDown()
            if (blockTerminateUntilReleased) {
                releaseTerminate.await()
            }
            terminateFailure?.let { throw it }
            return terminateStatus
        }
    }

    private fun allocateSurfaceWithoutCallingAndroidStub(): Surface {
        val unsafeClass = Class.forName("sun.misc.Unsafe")
        val field = unsafeClass.getDeclaredField("theUnsafe").apply { isAccessible = true }
        val unsafe = field.get(null)
        return unsafeClass
            .getMethod("allocateInstance", Class::class.java)
            .invoke(unsafe, Surface::class.java) as Surface
    }

    private object TestContext : ContextWrapper(null) {
        override fun getApplicationContext(): Context = this
    }

    private companion object {
        val IgnoreFailures: (String, Throwable) -> Unit = { _, _ -> }
    }
}
