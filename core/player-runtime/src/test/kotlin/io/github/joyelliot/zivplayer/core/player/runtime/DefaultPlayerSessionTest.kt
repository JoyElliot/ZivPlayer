// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player.runtime

import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaItem
import io.github.joyelliot.zivplayer.core.model.MediaSource
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.PlaybackRatePermille
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.model.VolumePercent
import io.github.joyelliot.zivplayer.core.player.CommandResult
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.PlaybackTimeline
import io.github.joyelliot.zivplayer.core.player.PlayerCapabilities
import io.github.joyelliot.zivplayer.core.player.PlayerCapability
import io.github.joyelliot.zivplayer.core.player.PlayerCommand
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.PlayerErrorKind
import io.github.joyelliot.zivplayer.core.player.PlayerSession
import io.github.joyelliot.zivplayer.core.player.PlayerStatus
import io.github.joyelliot.zivplayer.core.player.RepeatMode
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DefaultPlayerSessionTest {
    @Test
    fun setQueueLoadsSelectedItemAndPreparedMovesToReady() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        val result = dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))

        assertTrue(result is CommandResult.Accepted)
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        assertEquals(QueueItemId("two"), backend.loads.single().item.id)
        assertTrue(PlayerCapability.PLAY in session.snapshot.value.capabilities.available)
        assertTrue(PlayerCapability.STOP in session.snapshot.value.capabilities.available)
        assertFalse(PlayerCapability.PAUSE in session.snapshot.value.capabilities.available)

        val play = dispatch(session, PlayerCommand.Play)
        assertTrue(play is CommandResult.Accepted && play.changed)
        assertTrue(PlayerCapability.PAUSE in session.snapshot.value.capabilities.available)
        val pause = dispatch(session, PlayerCommand.Pause)
        assertTrue(pause is CommandResult.Accepted && pause.changed)

        backend.emit(
            BackendEvent.Prepared(
                generation = backend.loads.single().generation,
                duration = Milliseconds(30_000),
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()

        assertEquals(PlayerStatus.READY, session.snapshot.value.status)
        assertEquals(Milliseconds(30_000), session.snapshot.value.timeline.duration)
        advanceTimeBy(15_000L)
        runCurrent()
        assertEquals(PlayerStatus.READY, session.snapshot.value.status)
        close(session)
    }

    @Test
    fun autoplayWaitsForPreparedThenStartsAndCanPause() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), playWhenReady = true))
        assertEquals(0, backend.playCount)
        assertTrue(PlayerCapability.PAUSE in session.snapshot.value.capabilities.available)

        backend.emit(BackendEvent.PlaybackPaused(backend.loads.single().generation))
        runCurrent()
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        assertTrue(session.snapshot.value.playWhenReady)

        backend.emit(
            BackendEvent.Prepared(
                generation = backend.loads.single().generation,
                duration = null,
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()

        assertEquals(1, backend.playCount)
        assertEquals(PlayerStatus.PLAYING, session.snapshot.value.status)

        val pause = dispatch(session, PlayerCommand.Pause)
        assertTrue(pause is CommandResult.Accepted && pause.changed)
        assertEquals(1, backend.pauseCount)
        assertEquals(PlayerStatus.PAUSED, session.snapshot.value.status)
        assertFalse(session.snapshot.value.playWhenReady)
        close(session)
    }

    @Test
    fun staleEventsCannotOverwriteTheCurrentLoad() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 0))
        val firstGeneration = backend.loads.last().generation
        dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))
        val secondGeneration = backend.loads.last().generation
        assertNotEquals(firstGeneration, secondGeneration)

        backend.emit(BackendEvent.Prepared(firstGeneration, Milliseconds(999_000)))
        runCurrent()

        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        assertEquals(QueueItemId("two"), session.snapshot.value.queue.currentItem?.id)
        assertEquals(null, session.snapshot.value.timeline.duration)
        close(session)
    }

    @Test
    fun endOfItemAdvancesAndRepeatOneReloadsTheSameItem() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), playWhenReady = true))
        val firstGeneration = backend.loads.single().generation
        backend.emit(BackendEvent.Prepared(firstGeneration, Milliseconds(10_000), capabilities = allCapabilities()))
        runCurrent()
        backend.emit(BackendEvent.Ended(firstGeneration))
        runCurrent()

        assertEquals(2, backend.loads.size)
        assertEquals(QueueItemId("two"), backend.loads.last().item.id)
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)

        backend.emit(BackendEvent.Prepared(backend.loads.last().generation, Milliseconds(20_000), capabilities = allCapabilities()))
        runCurrent()
        dispatch(session, PlayerCommand.SetRepeatMode(RepeatMode.ONE))
        val secondGeneration = backend.loads.last().generation
        backend.emit(BackendEvent.Ended(secondGeneration))
        runCurrent()

        assertEquals(3, backend.loads.size)
        assertEquals(QueueItemId("two"), backend.loads.last().item.id)
        assertTrue(session.snapshot.value.playWhenReady)
        close(session)
    }

    @Test
    fun stopUnloadsButRetainsQueueAndPlayUsesANewGeneration() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        val firstGeneration = backend.loads.single().generation
        backend.emit(BackendEvent.Prepared(firstGeneration, Milliseconds(10_000), capabilities = allCapabilities()))
        runCurrent()

        dispatch(session, PlayerCommand.Stop)
        assertEquals(PlayerStatus.IDLE, session.snapshot.value.status)
        assertEquals(2, session.snapshot.value.queue.items.size)
        assertTrue(PlayerCapability.PLAY in session.snapshot.value.capabilities.available)
        assertEquals(1, backend.stopCount)

        dispatch(session, PlayerCommand.Play)
        assertEquals(2, backend.loads.size)
        assertNotEquals(firstGeneration, backend.loads.last().generation)
        assertTrue(session.snapshot.value.playWhenReady)
        close(session)
    }

    @Test
    fun stopBeforePreparedUnloadsTheActiveLoadAndIgnoresItsLateEvents() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        val generation = backend.loads.single().generation
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        assertTrue(PlayerCapability.STOP in session.snapshot.value.capabilities.available)

        val stop = dispatch(session, PlayerCommand.Stop)

        assertTrue(stop is CommandResult.Accepted)
        assertEquals(1, backend.stopCount)
        assertEquals(PlayerStatus.IDLE, session.snapshot.value.status)
        assertEquals(2, session.snapshot.value.queue.items.size)
        assertTrue(PlayerCapability.PLAY in session.snapshot.value.capabilities.available)

        advanceTimeBy(15_000L)
        runCurrent()
        assertEquals(PlayerStatus.IDLE, session.snapshot.value.status)

        backend.emit(BackendEvent.Prepared(generation, Milliseconds(99_000), capabilities = allCapabilities()))
        runCurrent()
        assertEquals(PlayerStatus.IDLE, session.snapshot.value.status)
        assertEquals(null, session.snapshot.value.timeline.duration)

        val play = dispatch(session, PlayerCommand.Play)
        assertTrue(play is CommandResult.Accepted)
        assertEquals(2, backend.loads.size)
        assertNotEquals(generation, backend.loads.last().generation)
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        close(session)
    }

    @Test
    fun seekOutsideKnownDurationIsRejectedWithoutBackendCall() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        backend.emit(
            BackendEvent.Prepared(
                backend.loads.single().generation,
                Milliseconds(10_000),
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()

        val result = dispatch(session, PlayerCommand.SeekTo(Milliseconds(10_001)))

        assertTrue(result is CommandResult.Rejected)
        assertEquals(0, backend.seekRequests.size)
        assertEquals(Milliseconds.ZERO, session.snapshot.value.timeline.position)
        close(session)
    }

    @Test
    fun backendFailureBecomesTypedErrorAndOldFailureIsIgnored() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 0))
        val obsolete = backend.loads.single().generation
        dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))
        val active = backend.loads.last().generation
        val error = PlayerError(
            kind = PlayerErrorKind.SOURCE_UNAVAILABLE,
            message = "The selected source is unavailable.",
            recovery = ErrorRecovery.SKIP,
        )

        backend.emit(BackendEvent.Failure(obsolete, error))
        runCurrent()
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)

        backend.emit(BackendEvent.Failure(active, error))
        runCurrent()
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(error, session.snapshot.value.error)

        backend.emit(BackendEvent.Prepared(active, Milliseconds(40_000), capabilities = allCapabilities()))
        backend.emit(BackendEvent.BufferingChanged(active, true))
        backend.emit(BackendEvent.Ended(active))
        runCurrent()
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(error, session.snapshot.value.error)
        close(session)
    }

    @Test
    fun backendAvailabilityIsPreservedAndUnsupportedCommandIsRejected() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        backend.emit(
            BackendEvent.Prepared(
                generation = backend.loads.single().generation,
                duration = Milliseconds(10_000),
                capabilities = PlayerCapabilities.of(
                    supported = setOf(PlayerCapability.PLAY),
                    backendAvailable = setOf(PlayerCapability.PLAY),
                ),
            ),
        )
        runCurrent()

        assertTrue(PlayerCapability.PLAY in session.snapshot.value.capabilities.available)
        assertFalse(PlayerCapability.STOP in session.snapshot.value.capabilities.available)
        val stop = dispatch(session, PlayerCommand.Stop)
        assertTrue(stop is CommandResult.Rejected)
        assertEquals(
            PlayerErrorKind.UNSUPPORTED_OPERATION,
            (stop as CommandResult.Rejected).error.kind,
        )
        assertEquals(0, backend.stopCount)
        close(session)
    }

    @Test
    fun seekWhileBufferingPreservesIntentAndWaitsForSeekCompletion() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), playWhenReady = true))
        val generation = backend.loads.single().generation
        backend.emit(BackendEvent.Prepared(generation, Milliseconds(20_000), capabilities = allCapabilities()))
        runCurrent()
        backend.emit(BackendEvent.BufferingChanged(generation, true))
        runCurrent()
        assertEquals(PlayerStatus.BUFFERING, session.snapshot.value.status)

        dispatch(session, PlayerCommand.SeekTo(Milliseconds(8_000)))
        assertTrue(session.snapshot.value.playWhenReady)
        backend.emit(
            BackendEvent.PositionChanged(
                generation,
                position = Milliseconds(2_000),
                duration = Milliseconds(20_000),
                bufferedPosition = Milliseconds(5_000),
            ),
        )
        runCurrent()
        assertEquals(Milliseconds(8_000), session.snapshot.value.timeline.position)

        backend.emit(
            BackendEvent.SeekCompleted(
                generation = generation,
                seekGeneration = backend.seekRequests.single().seekGeneration,
                position = Milliseconds(7_950),
            ),
        )
        backend.emit(BackendEvent.BufferingChanged(generation, false))
        runCurrent()

        assertEquals(Milliseconds(7_950), session.snapshot.value.timeline.position)
        assertEquals(PlayerStatus.PLAYING, session.snapshot.value.status)
        assertTrue(session.snapshot.value.playWhenReady)
        close(session)
    }

    @Test
    fun consecutiveSeeksAreCoalescedAndOnlyTheLatestCompletionIsCommitted() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), playWhenReady = true))
        val generation = backend.loads.single().generation
        backend.emit(BackendEvent.Prepared(generation, Milliseconds(20_000), capabilities = allCapabilities()))
        runCurrent()
        dispatch(session, PlayerCommand.Pause)

        dispatch(session, PlayerCommand.SeekTo(Milliseconds(5_000)))
        val firstSeek = backend.seekRequests.single()
        dispatch(session, PlayerCommand.SeekTo(Milliseconds(12_000)))
        assertEquals(1, backend.seekRequests.size)
        assertEquals(Milliseconds(12_000), session.snapshot.value.timeline.position)

        backend.emit(
            BackendEvent.SeekCompleted(
                generation = generation,
                seekGeneration = firstSeek.seekGeneration,
                position = Milliseconds(4_980),
            ),
        )
        runCurrent()

        assertEquals(2, backend.seekRequests.size)
        val latestSeek = backend.seekRequests.last()
        assertNotEquals(firstSeek.seekGeneration, latestSeek.seekGeneration)
        assertEquals(Milliseconds(12_000), latestSeek.position)
        assertEquals(Milliseconds(12_000), session.snapshot.value.timeline.position)

        backend.emit(
            BackendEvent.SeekCompleted(
                generation = generation,
                seekGeneration = firstSeek.seekGeneration,
                position = Milliseconds(4_980),
            ),
        )
        runCurrent()
        assertEquals(Milliseconds(12_000), session.snapshot.value.timeline.position)

        backend.emit(
            BackendEvent.SeekCompleted(
                generation = generation,
                seekGeneration = latestSeek.seekGeneration,
                position = Milliseconds(11_990),
            ),
        )
        runCurrent()

        assertEquals(Milliseconds(11_990), session.snapshot.value.timeline.position)
        assertEquals(PlayerStatus.PAUSED, session.snapshot.value.status)
        assertFalse(session.snapshot.value.playWhenReady)

        backend.emit(
            BackendEvent.PositionChanged(
                generation = generation,
                position = Milliseconds(1_000),
                duration = Milliseconds(20_000),
                bufferedPosition = Milliseconds(3_000),
            ),
        )
        runCurrent()
        assertEquals(Milliseconds(11_990), session.snapshot.value.timeline.position)

        backend.emit(
            BackendEvent.PositionChanged(
                generation = generation,
                position = Milliseconds(12_010),
                duration = Milliseconds(20_000),
                bufferedPosition = Milliseconds(15_000),
                seekGeneration = latestSeek.seekGeneration,
            ),
        )
        runCurrent()
        assertEquals(Milliseconds(12_010), session.snapshot.value.timeline.position)
        close(session)
    }

    @Test
    fun endedArrivingAfterPauseHonorsTheLatestPauseIntent() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), playWhenReady = true))
        val generation = backend.loads.single().generation
        backend.emit(BackendEvent.Prepared(generation, Milliseconds(10_000), capabilities = allCapabilities()))
        runCurrent()
        dispatch(session, PlayerCommand.Pause)

        backend.emit(BackendEvent.Ended(generation))
        runCurrent()

        assertEquals(PlayerStatus.ENDED, session.snapshot.value.status)
        assertFalse(session.snapshot.value.playWhenReady)
        assertEquals(1, backend.loads.size)
        assertEquals(QueueItemId("one"), session.snapshot.value.queue.currentItem?.id)
        close(session)
    }

    @Test
    fun completedBackendEventStreamIsTerminalAndCannotCreateAZombieLoad() = runTest {
        val backend = FakeBackend(eventFlow = emptyFlow())
        val session = newSession(backend)

        val result = dispatch(session, PlayerCommand.SetQueue(items()))

        assertTrue(result is CommandResult.Rejected)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertTrue(backend.loads.isEmpty())
        close(session)
    }

    @Test
    fun backendEventStreamCancellationIsReportedAsTerminalFailure() = runTest {
        val backend = FakeBackend(
            eventFlow = flow<BackendEvent> {
                throw CancellationException("upstream event transport stopped")
            },
        )
        val session = newSession(backend)
        runCurrent()

        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        val result = dispatch(session, PlayerCommand.SetQueue(items()))
        assertTrue(result is CommandResult.Rejected)
        assertTrue(backend.loads.isEmpty())
        close(session)
    }

    @Test
    fun eventStreamTerminationBlocksAQueuedLoadBeforeTheActorReceivesItsMarker() = runTest {
        val streamGate = CompletableDeferred<Unit>()
        val loadGate = CompletableDeferred<Unit>()
        val backend = FakeBackend(
            eventFlow = flow {
                streamGate.await()
            },
        ).apply {
            this.loadGate = loadGate
        }
        val session = newSession(backend)
        runCurrent()

        val firstLoad = async { session.dispatch(PlayerCommand.SetQueue(items(), startIndex = 0)) }
        runCurrent()
        assertEquals(1, backend.loads.size)

        val queuedLoad = async { session.dispatch(PlayerCommand.SetQueue(items(), startIndex = 1)) }
        runCurrent()
        streamGate.complete(Unit)
        runCurrent()
        loadGate.complete(Unit)
        runCurrent()

        assertTrue(firstLoad.await() is CommandResult.Accepted)
        assertTrue(queuedLoad.await() is CommandResult.Failed)
        assertEquals(1, backend.loads.size)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        close(session)
    }

    @Test
    fun loadWithoutPreparedOrFailureEventTimesOutInsteadOfRemainingLoading() = runTest {
        val backend = FakeBackend().apply {
            loadGate = CompletableDeferred()
        }
        val session = newSession(
            backend,
            loadReadinessTimeoutMillis = 1_000L,
            backendOperationTimeoutMillis = 10_000L,
        )
        runCurrent()

        val load = async { session.dispatch(PlayerCommand.SetQueue(items())) }
        runCurrent()
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        assertFalse(load.isCompleted)

        advanceTimeBy(1_000L)
        runCurrent()

        assertTrue(load.await() is CommandResult.Failed)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(ErrorRecovery.RESET, session.snapshot.value.error?.recovery)
        backend.emit(
            BackendEvent.Prepared(
                backend.loads.single().generation,
                Milliseconds(10_000),
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)

        val retry = dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))
        assertTrue(retry is CommandResult.Rejected)
        assertEquals(1, backend.loads.size)
        assertFalse(PlayerCapability.PLAY in session.snapshot.value.capabilities.available)
        assertFalse(PlayerCapability.STOP in session.snapshot.value.capabilities.available)
        assertFalse(PlayerCapability.SET_VOLUME in session.snapshot.value.capabilities.available)
        assertFalse(PlayerCapability.PLAY in session.snapshot.value.capabilities.backendAvailable)
        assertFalse(PlayerCapability.STOP in session.snapshot.value.capabilities.backendAvailable)

        val volume = dispatch(session, PlayerCommand.SetVolume(VolumePercent(25)))
        assertTrue(volume is CommandResult.Rejected)
        assertFalse("volume" in backend.callOrder)

        val stop = dispatch(session, PlayerCommand.Stop)
        assertTrue(stop is CommandResult.Rejected)
        assertEquals(0, backend.stopCount)

        val clear = dispatch(session, PlayerCommand.ClearQueue)
        assertTrue(clear is CommandResult.Accepted)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertTrue(session.snapshot.value.queue.items.isEmpty())
        assertEquals(ErrorRecovery.RESET, session.snapshot.value.error?.recovery)
        close(session)
    }

    @Test
    fun loadOperationTimeoutRequiresResetBeforeTheReadinessDeadline() = runTest {
        val backend = FakeBackend().apply {
            loadGate = CompletableDeferred()
        }
        val session = newSession(
            backend,
            loadReadinessTimeoutMillis = 10_000L,
            backendOperationTimeoutMillis = 1_000L,
        )
        runCurrent()

        val load = async { session.dispatch(PlayerCommand.SetQueue(items())) }
        runCurrent()
        advanceTimeBy(1_000L)
        runCurrent()

        assertTrue(load.await() is CommandResult.Failed)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(ErrorRecovery.RESET, session.snapshot.value.error?.recovery)
        val retry = dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))
        assertTrue(retry is CommandResult.Rejected)
        assertEquals(1, backend.loads.size)
        close(session)
    }

    @Test
    fun stopTimeoutMakesTheBackendNonReusable() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend, backendOperationTimeoutMillis = 1_000L)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        backend.emit(
            BackendEvent.Prepared(
                backend.loads.single().generation,
                Milliseconds(10_000),
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()
        backend.stopGate = CompletableDeferred()

        val stop = async { session.dispatch(PlayerCommand.Stop) }
        runCurrent()
        advanceTimeBy(1_000L)
        runCurrent()

        assertTrue(stop.await() is CommandResult.Failed)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(ErrorRecovery.RESET, session.snapshot.value.error?.recovery)
        val retry = dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))
        assertTrue(retry is CommandResult.Rejected)
        assertEquals(1, backend.loads.size)
        close(session)
    }

    @Test
    fun stopFailureMakesTheBackendNonReusable() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        backend.emit(
            BackendEvent.Prepared(
                backend.loads.single().generation,
                Milliseconds(10_000),
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()
        backend.stopFailure = IllegalStateException("native stop failed")

        val stop = dispatch(session, PlayerCommand.Stop)

        assertTrue(stop is CommandResult.Failed)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(ErrorRecovery.RESET, session.snapshot.value.error?.recovery)
        val retry = dispatch(session, PlayerCommand.SetQueue(items(), startIndex = 1))
        assertTrue(retry is CommandResult.Rejected)
        assertEquals(1, backend.loads.size)
        close(session)
    }

    @Test
    fun autoplayRequiresThePreparedBackendToExposePlay() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items(), playWhenReady = true))
        backend.emit(
            BackendEvent.Prepared(
                backend.loads.single().generation,
                Milliseconds(10_000),
                capabilities = PlayerCapabilities.NONE,
            ),
        )
        runCurrent()

        assertEquals(0, backend.playCount)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertEquals(PlayerErrorKind.UNSUPPORTED_OPERATION, session.snapshot.value.error?.kind)
        assertEquals(ErrorRecovery.RESET, session.snapshot.value.error?.recovery)
        close(session)
    }

    @Test
    fun terminalEndIgnoresLateEventsAndPlayCreatesANewLoad() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(listOf(queueItem("only")), playWhenReady = true))
        val generation = backend.loads.single().generation
        backend.emit(BackendEvent.Prepared(generation, Milliseconds(1_000), capabilities = allCapabilities()))
        runCurrent()
        backend.emit(BackendEvent.Ended(generation))
        runCurrent()
        assertEquals(PlayerStatus.ENDED, session.snapshot.value.status)

        backend.emit(BackendEvent.PlaybackStarted(generation))
        backend.emit(BackendEvent.BufferingChanged(generation, true))
        runCurrent()
        assertEquals(PlayerStatus.ENDED, session.snapshot.value.status)

        dispatch(session, PlayerCommand.Play)
        assertEquals(2, backend.loads.size)
        assertNotEquals(generation, backend.loads.last().generation)
        assertEquals(PlayerStatus.LOADING, session.snapshot.value.status)
        close(session)
    }

    @Test
    fun backendCancellationBecomesFailureWithoutKillingActor() = runTest {
        val backend = FakeBackend()
        val session = newSession(backend)
        runCurrent()

        dispatch(session, PlayerCommand.SetQueue(items()))
        backend.emit(
            BackendEvent.Prepared(
                backend.loads.single().generation,
                Milliseconds(10_000),
                capabilities = allCapabilities(),
            ),
        )
        runCurrent()
        backend.playFailure = CancellationException("backend cancelled itself")

        val play = dispatch(session, PlayerCommand.Play)

        assertTrue(play is CommandResult.Failed)
        assertEquals(PlayerStatus.ERROR, session.snapshot.value.status)
        assertTrue(PlayerCapability.STOP in session.snapshot.value.capabilities.available)
        val stop = dispatch(session, PlayerCommand.Stop)
        assertTrue(stop is CommandResult.Accepted)
        assertEquals(1, backend.stopCount)
        assertEquals(PlayerStatus.IDLE, session.snapshot.value.status)
        close(session)
        assertEquals(1, backend.closeCount)
    }

    @Test
    fun cancelledCloseStillFinishesAndSlowEventConsumerCannotBlockCommands() = runTest {
        val backend = FakeBackend().apply {
            closeGate = CompletableDeferred()
        }
        val session = DefaultPlayerSession(
            backend = backend,
            dispatcher = StandardTestDispatcher(testScheduler),
            eventBufferCapacity = 1,
        )
        val eventGate = CompletableDeferred<Unit>()
        val eventCollector = launch {
            session.events.collect {
                eventGate.await()
            }
        }
        runCurrent()

        repeat(10) { index ->
            val mode = if (index % 2 == 0) RepeatMode.ALL else RepeatMode.OFF
            assertTrue(dispatch(session, PlayerCommand.SetRepeatMode(mode)) is CommandResult.Accepted)
        }

        val firstClose = launch { session.close() }
        runCurrent()
        firstClose.cancel()
        backend.closeGate!!.complete(Unit)
        runCurrent()
        firstClose.join()

        session.close()
        assertEquals(1, backend.closeCount)
        assertEquals(PlayerStatus.CLOSED, session.snapshot.value.status)
        eventGate.complete(Unit)
        eventCollector.cancel()
    }

    @Test
    fun actorSerializesCommandsAndCloseIsExactlyOnce() = runTest {
        val backend = FakeBackend().apply {
            loadGate = CompletableDeferred()
        }
        val session = newSession(backend)
        runCurrent()

        val setQueue = async { session.dispatch(PlayerCommand.SetQueue(items())) }
        runCurrent()
        val play = async { session.dispatch(PlayerCommand.Play) }
        runCurrent()

        assertFalse(setQueue.isCompleted)
        assertFalse(play.isCompleted)
        assertEquals(listOf("load"), backend.callOrder)

        backend.loadGate!!.complete(Unit)
        runCurrent()

        assertTrue(setQueue.await() is CommandResult.Accepted)
        assertTrue(play.await() is CommandResult.Accepted)
        assertTrue(session.snapshot.value.playWhenReady)
        assertEquals(0, backend.playCount)

        val closeOne = async { session.close() }
        val closeTwo = async { session.close() }
        runCurrent()
        closeOne.await()
        closeTwo.await()

        assertEquals(1, backend.closeCount)
        assertEquals(PlayerStatus.CLOSED, session.snapshot.value.status)
        val afterClose = session.dispatch(PlayerCommand.Play)
        assertTrue(afterClose is CommandResult.Rejected)
        assertEquals(PlayerErrorKind.SESSION_CLOSED, (afterClose as CommandResult.Rejected).error.kind)
    }

    private fun TestScope.newSession(
        backend: FakeBackend,
        loadReadinessTimeoutMillis: Long = 15_000L,
        backendOperationTimeoutMillis: Long = 10_000L,
    ): DefaultPlayerSession =
        DefaultPlayerSession(
            backend = backend,
            dispatcher = StandardTestDispatcher(testScheduler),
            loadReadinessTimeoutMillis = loadReadinessTimeoutMillis,
            backendOperationTimeoutMillis = backendOperationTimeoutMillis,
        )

    private suspend fun TestScope.dispatch(
        session: PlayerSession,
        command: PlayerCommand,
    ): CommandResult {
        val result = async { session.dispatch(command) }
        runCurrent()
        return result.await()
    }

    private suspend fun TestScope.close(session: PlayerSession) {
        val close = async { session.close() }
        runCurrent()
        close.await()
    }

    private fun items(): List<QueueItem> = listOf(queueItem("one"), queueItem("two"))

    private fun queueItem(id: String): QueueItem =
        QueueItem(
            id = QueueItemId(id),
            media = MediaItem(
                id = MediaId("media-$id"),
                source = MediaSource("https://example.test/$id.mkv"),
            ),
        )

    private fun allCapabilities(): PlayerCapabilities =
        PlayerCapabilities.of(PlayerCapability.entries.toSet())

    private class FakeBackend(
        eventFlow: Flow<BackendEvent>? = null,
    ) : PlayerBackend {
        private val mutableEvents = MutableSharedFlow<BackendEvent>(extraBufferCapacity = 32)
        override val events: Flow<BackendEvent> = eventFlow ?: mutableEvents

        val loads = mutableListOf<BackendLoadRequest>()
        val seekRequests = mutableListOf<BackendSeekRequest>()
        val callOrder = mutableListOf<String>()
        var playCount = 0
        var pauseCount = 0
        var stopCount = 0
        var closeCount = 0
        var loadGate: CompletableDeferred<Unit>? = null
        var stopGate: CompletableDeferred<Unit>? = null
        var closeGate: CompletableDeferred<Unit>? = null
        var playFailure: Throwable? = null
        var stopFailure: Throwable? = null

        suspend fun emit(event: BackendEvent) {
            mutableEvents.emit(event)
        }

        override suspend fun load(request: BackendLoadRequest) {
            callOrder += "load"
            loads += request
            loadGate?.await()
        }

        override suspend fun play() {
            callOrder += "play"
            playFailure?.let { throw it }
            playCount += 1
        }

        override suspend fun pause() {
            callOrder += "pause"
            pauseCount += 1
        }

        override suspend fun seekTo(request: BackendSeekRequest) {
            callOrder += "seek"
            seekRequests += request
        }

        override suspend fun stop() {
            callOrder += "stop"
            stopCount += 1
            stopFailure?.let { throw it }
            stopGate?.await()
        }

        override suspend fun setVolume(volume: VolumePercent) {
            callOrder += "volume"
        }

        override suspend fun setPlaybackRate(playbackRate: PlaybackRatePermille) {
            callOrder += "rate"
        }

        override suspend fun setMuted(muted: Boolean) {
            callOrder += "mute"
        }

        override suspend fun selectTrack(kind: TrackKind, trackId: TrackId?) {
            callOrder += "track"
        }

        override suspend fun close() {
            callOrder += "close"
            closeCount += 1
            closeGate?.await()
        }
    }
}
