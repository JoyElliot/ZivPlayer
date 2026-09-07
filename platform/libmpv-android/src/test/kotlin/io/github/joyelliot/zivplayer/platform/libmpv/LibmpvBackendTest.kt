// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.content.ContextWrapper
import android.view.Surface
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaItem
import io.github.joyelliot.zivplayer.core.model.MediaSource
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.DecoderMode
import io.github.joyelliot.zivplayer.core.model.RenderQuality
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.PlayerErrorKind
import io.github.joyelliot.zivplayer.core.player.runtime.BackendEvent
import io.github.joyelliot.zivplayer.core.player.runtime.BackendLoadRequest
import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class LibmpvBackendTest {
    @Test
    fun replacementClosesPreviousSourceOnlyAfterNativeEnd() = runBlocking {
        val client = FakeMpvClient()
        val events = mutableListOf<String>()
        var serial = 0
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" },
            MpvMediaSourceOpener { val id = ++serial; events += "open$id"; OpenMpvMediaSource("fd://$id") { events += "close$id" } })
        client.commandHandler = { args -> if (args[0] == "stop") {
            events += "stop"
            assertEquals(listOf("open1", "stop"), events)
            client.emit(MpvClientEvent(MpvEventType.END_FILE.rawValue, rawEndReason = MpvEndFileReason.STOP.rawValue))
        } }
        backend.load(loadRequest())
        client.emit(MpvClientEvent(MpvEventType.START_FILE.rawValue))
        backend.load(loadRequest().copy(generation = LoadGeneration(2)))
        assertEquals(listOf("open1", "stop", "close1", "open2"), events)
        assertEquals(listOf("fd://1", "fd://2"), client.commands.filter { it.first() == "loadfile" }.map { it[1] })
        backend.close()
        assertEquals("close2", events.last())
    }

    @Test
    fun pumpFailureDuringStopRetainsBorrowedSourceUntilDestroy() = runBlocking {
        val client = FakeMpvClient(destroyFailuresRemaining = 1)
        var closes = 0
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" },
            MpvMediaSourceOpener { OpenMpvMediaSource("fd://12") { closes++ } })
        backend.load(loadRequest())
        client.emit(MpvClientEvent(MpvEventType.START_FILE.rawValue))
        client.commandHandler = { if (it[0] == "stop") client.fail(MpvClientFailure.EVENT_PUMP_STOPPED) }
        assertNotNull(runCatching { backend.stop() }.exceptionOrNull())
        assertEquals(0, closes)
        assertNotNull(runCatching { backend.close() }.exceptionOrNull())
        assertEquals(0, closes)
        backend.close()
        assertEquals(1, closes)
    }

    @Test
    fun failedLoadCannotCloseBorrowedSourceThroughStop() = runBlocking {
        val client = FakeMpvClient()
        var closes = 0
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" },
            MpvMediaSourceOpener { OpenMpvMediaSource("fd://12") { closes++ } })
        client.commandHandler = { if (it[0] == "loadfile") error("uncertain command completion") }
        assertNotNull(runCatching { backend.load(loadRequest()) }.exceptionOrNull())
        assertNotNull(runCatching { backend.stop() }.exceptionOrNull())
        assertEquals(0, closes)
        backend.close()
        assertEquals(1, closes)
    }

    @Test
    fun sourceCloseFailureCanRetryAfterSuccessfulNativeDestroy() = runBlocking {
        val client = FakeMpvClient()
        var attempts = 0
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" },
            MpvMediaSourceOpener { OpenMpvMediaSource("fd://12") { if (++attempts == 1) error("close failed") } })
        backend.load(loadRequest())
        assertNotNull(runCatching { backend.close() }.exceptionOrNull())
        backend.close()
        backend.close()
        assertEquals(2, attempts)
        assertEquals(1, client.destroyCalls)
    }

    @Test
    fun configurationRollsBackTheSetterThatMutatedThenFailed() = runBlocking {
        val client = FakeMpvClient()
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })
        backend.load(loadRequest())
        val before = client.strings.toMap()
        client.stringFailure = { name, value -> name == "scale" && value == "ewa_lanczossharp" }
        assertNotNull(runCatching { backend.configure(PlaybackOptions(decoder = DecoderMode.SOFTWARE,
            quality = RenderQuality.HIGH), LibmpvResourcePaths()) }.exceptionOrNull())
        assertEquals(before, client.strings)
        assertEquals(listOf("hwdec", "scale", "scale", "hwdec"), client.stringWrites.takeLast(4))
        client.stringFailure = { _, _ -> false }
        backend.play() // A fully restored engine remains usable.
        backend.close()
    }

    @Test
    fun failedConfigurationRollbackRequiresReset() = runBlocking {
        val client = FakeMpvClient()
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })
        backend.load(loadRequest())
        client.stringFailure = { name, _ -> name == "scale" }
        assertNotNull(runCatching { backend.configure(PlaybackOptions(quality = RenderQuality.HIGH), LibmpvResourcePaths()) }.exceptionOrNull())
        val failure = withTimeout(1_000) { backend.events.take(1).toList().single() } as BackendEvent.Failure
        assertEquals(ErrorRecovery.RESET, failure.error.recovery)
        assertNotNull(runCatching { backend.play() }.exceptionOrNull())
        backend.close()
    }

    @Test
    fun eventBurstPublishesOneResetAndStopsFurtherDelivery() = runBlocking {
        val client = FakeMpvClient()
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })
        backend.load(loadRequest())
        client.emit(MpvClientEvent(rawEventId = MpvEventType.START_FILE.rawValue))
        client.emit(MpvClientEvent(rawEventId = MpvEventType.FILE_LOADED.rawValue))
        repeat(10_000) { index -> client.property(MpvPropertyChange("time-pos", MpvPropertyFormat.DOUBLE.rawValue,
            MpvPropertyValue.DoubleValue(index.toDouble()), 1L)) }
        val failure = withTimeout(1_000) { backend.events.take(1).toList().single() } as BackendEvent.Failure
        assertEquals(ErrorRecovery.RESET, failure.error.recovery)
        assertEquals(true, client.flags["pause"])
        assertNotNull(runCatching { backend.play() }.exceptionOrNull())
        backend.close()
    }

    @Test
    fun pumpFailureStillPublishesResetWhenTheDeliveryQueueIsExactlyFull() = runBlocking {
        val client = FakeMpvClient()
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })
        backend.load(loadRequest())
        client.emit(MpvClientEvent(rawEventId = MpvEventType.START_FILE.rawValue))
        repeat(256) { client.property(MpvPropertyChange("time-pos", MpvPropertyFormat.DOUBLE.rawValue,
            MpvPropertyValue.DoubleValue(1.0), 1L)) }
        client.fail(MpvClientFailure.EVENT_PUMP_STOPPED)
        val failure = withTimeout(1_000) { backend.events.take(1).toList().single() } as BackendEvent.Failure
        assertEquals(ErrorRecovery.RESET, failure.error.recovery)
        backend.close()
    }

    @Test
    fun failedCreationIsNotRetriedByLoadCleanup() = runBlocking {
        var createCalls = 0
        val creationFailure = IllegalStateException("expected creation failure")
        val backend = LibmpvBackend(
            context = TestContext,
            clientFactory = MpvClientFactory {
                createCalls += 1
                throw creationFailure
            },
            prepareConfigDirectory = { "/test/mpv" },
        )

        val actualFailure = runCatching {
            backend.load(loadRequest())
        }.exceptionOrNull()

        assertSame(creationFailure, actualFailure)
        assertEquals(1, createCalls)
        backend.close()
    }

    @Test
    fun endFileErrorIsNotReportedAsNaturalCompletion() = runBlocking {
        val client = FakeMpvClient()
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })

        backend.load(loadRequest())
        client.emit(MpvClientEvent(rawEventId = MpvEventType.START_FILE.rawValue))
        client.emit(MpvClientEvent(rawEventId = MpvEventType.FILE_LOADED.rawValue))
        client.emit(
            MpvClientEvent(
                rawEventId = MpvEventType.END_FILE.rawValue,
                rawEndReason = MpvEndFileReason.ERROR.rawValue,
                endErrorCode = -13,
            ),
        )

        val events = withTimeout(1_000) { backend.events.take(2).toList() }
        assertTrue(events[0] is BackendEvent.Prepared)
        val failure = events[1] as BackendEvent.Failure
        assertEquals(PlayerErrorKind.SOURCE_UNAVAILABLE, failure.error.kind)
        assertEquals("libmpv could not play the media (error -13).", failure.error.message)
        backend.close()
    }

    @Test
    fun eventPumpFailureFailsActiveGenerationAndRequiresBackendReset() = runBlocking {
        val client = FakeMpvClient()
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })

        backend.load(loadRequest())
        client.fail(MpvClientFailure.EVENT_PUMP_STOPPED)

        val failure = withTimeout(1_000) { backend.events.take(1).toList().single() }
            as BackendEvent.Failure
        assertEquals(PlayerErrorKind.BACKEND_OPERATION_FAILED, failure.error.kind)
        assertEquals(ErrorRecovery.RESET, failure.error.recovery)
        assertEquals("The libmpv event pump stopped unexpectedly.", failure.error.message)
        assertTrue(runCatching { backend.play() }.exceptionOrNull() is IllegalStateException)
        backend.close()
    }

    @Test
    fun failedNativeTeardownIsRetainedForALaterClose() = runBlocking {
        val client = FakeMpvClient(destroyFailuresRemaining = 1)
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })
        backend.play()

        val firstFailure = runCatching { backend.close() }.exceptionOrNull()

        assertNotNull(firstFailure)
        assertEquals(1, client.destroyCalls)
        backend.close()
        assertEquals(2, client.destroyCalls)
        backend.close()
        assertEquals(2, client.destroyCalls)
    }

    @Test
    fun observerRemovalFailureIsReportedAfterSuccessfulDestroy() = runBlocking {
        val removalFailure = IllegalStateException("expected observer removal failure")
        val client = FakeMpvClient(observerRemovalFailure = removalFailure)
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client }, { "/test/mpv" })
        backend.play()

        val actualFailure = runCatching { backend.close() }.exceptionOrNull()

        assertSame(removalFailure, actualFailure)
        assertEquals(1, client.destroyCalls)
        backend.close()
        assertEquals(1, client.destroyCalls)
    }

    private fun loadRequest() = BackendLoadRequest(
        generation = LoadGeneration(1),
        item = QueueItem(
            id = QueueItemId("queue-item"),
            media = MediaItem(
                id = MediaId("media-item"),
                source = MediaSource("content://test/media-item"),
            ),
        ),
        startPosition = Milliseconds.ZERO,
    )

    private class FakeMpvClient(
        private var destroyFailuresRemaining: Int = 0,
        private val observerRemovalFailure: Throwable? = null,
    ) : MpvClient {
        private var observer: MpvClient.Observer? = null
        var destroyCalls = 0
            private set
        val strings = mutableMapOf<String, String>()
        val stringWrites = mutableListOf<String>()
        val flags = mutableMapOf<String, Boolean>()
        var stringFailure: (String, String) -> Boolean = { _, _ -> false }
        var commandHandler: (Array<String>) -> Unit = {}
        val commands = mutableListOf<List<String>>()

        override fun addObserver(observer: MpvClient.Observer) {
            check(this.observer == null)
            this.observer = observer
        }

        override fun removeObserver(observer: MpvClient.Observer) {
            observerRemovalFailure?.let { throw it }
            if (this.observer === observer) {
                this.observer = null
            }
        }

        override fun setOptionString(name: String, value: String) { strings[name] = value }

        override fun initialize() = Unit

        override fun command(arguments: Array<String>) { commands += arguments.toList(); commandHandler(arguments) }

        override fun getPropertyDouble(name: String): Double? = null

        override fun getPropertyBoolean(name: String): Boolean? = null

        override fun setPropertyDouble(name: String, value: Double) = Unit

        override fun setPropertyBoolean(name: String, value: Boolean) { flags[name] = value }

        override fun setPropertyString(name: String, value: String) {
            strings[name] = value
            stringWrites += name
            check(!stringFailure(name, value)) { "Injected mutate-then-fail setter." }
        }

        override fun observeProperty(
            name: String,
            format: MpvPropertyFormat,
            replyUserdata: Long,
        ) = Unit

        override fun unobserveProperty(replyUserdata: Long): Int = 0

        override fun attachSurface(surface: Surface) = Unit

        override fun detachSurface() = Unit

        override fun destroy() {
            destroyCalls += 1
            if (destroyFailuresRemaining > 0) {
                destroyFailuresRemaining -= 1
                throw IllegalStateException("expected destroy failure")
            }
        }

        fun emit(event: MpvClientEvent) {
            checkNotNull(observer).onEvent(event)
        }

        fun property(change: MpvPropertyChange) { checkNotNull(observer).onPropertyChanged(change) }

        fun fail(failure: MpvClientFailure) {
            checkNotNull(observer).onFailure(failure)
        }
    }

    private object TestContext : ContextWrapper(null) {
        override fun getApplicationContext(): Context = this
    }
}
