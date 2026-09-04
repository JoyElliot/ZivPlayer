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
    fun failedCreationIsNotRetriedByLoadCleanup() = runBlocking {
        var createCalls = 0
        val creationFailure = IllegalStateException("expected creation failure")
        val backend = LibmpvBackend(
            context = TestContext,
            clientFactory = MpvClientFactory {
                createCalls += 1
                throw creationFailure
            },
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
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client })

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
    fun failedNativeTeardownIsRetainedForALaterClose() = runBlocking {
        val client = FakeMpvClient(destroyFailuresRemaining = 1)
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client })
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
        val backend = LibmpvBackend(TestContext, MpvClientFactory { client })
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

        override fun setOptionString(name: String, value: String) = Unit

        override fun initialize() = Unit

        override fun command(arguments: Array<String>) = Unit

        override fun getPropertyDouble(name: String): Double? = null

        override fun getPropertyBoolean(name: String): Boolean? = null

        override fun setPropertyDouble(name: String, value: Double) = Unit

        override fun setPropertyBoolean(name: String, value: Boolean) = Unit

        override fun setPropertyString(name: String, value: String) = Unit

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
    }

    private object TestContext : ContextWrapper(null) {
        override fun getApplicationContext(): Context = this
    }
}
