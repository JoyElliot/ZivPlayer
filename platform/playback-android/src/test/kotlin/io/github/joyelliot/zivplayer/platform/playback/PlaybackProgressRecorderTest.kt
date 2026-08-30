// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.MediaRecord
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.media.RecentMedia
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaItem
import io.github.joyelliot.zivplayer.core.model.MediaSource
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.QueueItem
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.player.ItemTransitionReason
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.PlaybackEvent
import io.github.joyelliot.zivplayer.core.player.PlaybackQueue
import io.github.joyelliot.zivplayer.core.player.PlaybackSnapshot
import io.github.joyelliot.zivplayer.core.player.PlaybackTimeline
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.PlayerErrorKind
import io.github.joyelliot.zivplayer.core.player.PlayerStatus
import io.github.joyelliot.zivplayer.core.player.StateChangeCause
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PlaybackProgressRecorderTest {
    @Test
    fun `active sampling persists stable media identity`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val playing = snapshot(
            status = PlayerStatus.PLAYING,
            mediaId = "stable-media",
            queueItemId = "private-occurrence",
            positionMs = 12_000L,
            durationMs = 120_000L,
        )

        recorder.bind(EPOCH_ONE, playing)
        awaitRecorder { recorder.sampleNow() }

        assertEquals(1, repository.checkpoints.size)
        with(repository.checkpoints.single()) {
            assertEquals(MediaId("stable-media"), mediaId)
            assertFalse(mediaId.value == "private-occurrence")
            assertEquals(Milliseconds(12_000L), position)
            assertEquals(Milliseconds(120_000L), duration)
            assertFalse(completed)
        }
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, playing) }
        assertEquals(1, repository.checkpoints.size)
    }

    @Test
    fun `active ticker samples once per configured period`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = PlaybackProgressRecorder(
            repository = repository,
            clock = { 1_000L },
            samplePeriodMs = 1_000L,
            ioDispatcher = StandardTestDispatcher(testScheduler),
        )
        val playing = snapshot(PlayerStatus.BUFFERING, positionMs = 15L, durationMs = null)

        recorder.bind(EPOCH_ONE, playing)
        runCurrent()
        advanceTimeBy(999L)
        runCurrent()
        assertTrue(repository.checkpoints.isEmpty())
        advanceTimeBy(1L)
        runCurrent()
        assertEquals(1, repository.checkpoints.size)
        assertEquals(null, repository.checkpoints.single().duration)

        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, playing) }
    }

    @Test
    fun `latest snapshots use strictly monotonic timestamps`() = runTest {
        val repository = FakeRecentMediaRepository()
        var clock = 500L
        val recorder = recorder(repository, clock = { clock })
        val first = snapshot(PlayerStatus.PLAYING, positionMs = 10L, durationMs = 100L)
        val second = snapshot(PlayerStatus.PLAYING, positionMs = 20L, durationMs = 100L)
        val paused = snapshot(PlayerStatus.PAUSED, positionMs = 30L, durationMs = 100L)

        recorder.bind(EPOCH_ONE, first)
        awaitRecorder { recorder.sampleNow() }
        recorder.observeSnapshot(EPOCH_ONE, second)
        awaitRecorder { recorder.sampleNow() }
        clock = 400L
        awaitRecorder { recorder.flushPause(EPOCH_ONE, paused) }

        assertEquals(listOf(10L, 20L, 30L), repository.checkpoints.map { it.position.value })
        assertEquals(listOf(500L, 501L, 502L), repository.checkpoints.map { it.updatedAt.value })
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, paused) }
    }

    @Test
    fun `persisted timestamp seeds a new recorder after clock rollback`() = runTest {
        val stored = PlaybackCheckpoint(
            mediaId = MediaId(DEFAULT_MEDIA_ID),
            position = Milliseconds(20L),
            duration = Milliseconds(100L),
            completed = false,
            updatedAt = EpochMilliseconds(5_000L),
        )
        val repository = FakeRecentMediaRepository(storedCheckpoint = stored)
        val recorder = recorder(repository, clock = { 1_000L })
        val playing = snapshot(PlayerStatus.PLAYING, positionMs = 30L, durationMs = 100L)

        recorder.bind(EPOCH_ONE, playing)
        awaitRecorder { recorder.sampleNow() }

        assertEquals(1, repository.checkpoints.size)
        assertEquals(5_001L, repository.checkpoints.single().updatedAt.value)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, playing) }
    }

    @Test
    fun `explicit stop writes zero while retaining known duration`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val playing = snapshot(PlayerStatus.PLAYING, positionMs = 70L, durationMs = 120L)
        val stopped = snapshot(PlayerStatus.IDLE, positionMs = 0L, durationMs = null)

        recorder.bind(EPOCH_ONE, playing)
        awaitRecorder { recorder.flushStop(EPOCH_ONE, stopped) }

        with(repository.checkpoints.single()) {
            assertEquals(Milliseconds.ZERO, position)
            assertEquals(Milliseconds(120L), duration)
            assertFalse(completed)
        }
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, stopped) }
    }

    @Test
    fun `ended state event is the completion source without a transition event`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val playing = snapshot(PlayerStatus.PLAYING, positionMs = 95L, durationMs = 100L)
        val ended = snapshot(
            PlayerStatus.ENDED,
            positionMs = 100L,
            durationMs = 100L,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, playing)
        recorder.observeEvent(EPOCH_ONE, stateChanged(ended, sequence = 1L))
        awaitRecorder { recorder.sampleNow() }

        with(repository.checkpoints.single()) {
            assertEquals(Milliseconds(100L), position)
            assertTrue(completed)
        }
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, ended) }
    }

    @Test
    fun `reset error cannot downgrade an ended checkpoint`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val ended = snapshot(
            PlayerStatus.ENDED,
            positionMs = 100L,
            durationMs = 100L,
            revision = 1L,
        )
        val resetError = ended.copy(
            status = PlayerStatus.ERROR,
            timeline = ended.timeline.copy(position = Milliseconds(90L)),
            error = PlayerError(
                kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
                message = "Injected reset error.",
                recovery = ErrorRecovery.RESET,
            ),
            revision = 2L,
        )

        recorder.bind(EPOCH_ONE, ended)
        awaitRecorder { recorder.flushAndDetach(EPOCH_ONE, resetError) }

        assertEquals(1, repository.checkpoints.size)
        assertTrue(repository.checkpoints.single().completed)
        assertEquals(Milliseconds(100L), repository.checkpoints.single().position)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, resetError) }
    }

    @Test
    fun `pause flush cannot downgrade an ended checkpoint`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val ended = snapshot(
            PlayerStatus.ENDED,
            positionMs = 100L,
            durationMs = 100L,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, ended)
        awaitRecorder { recorder.flushPause(EPOCH_ONE, ended) }

        assertEquals(1, repository.checkpoints.size)
        assertTrue(repository.checkpoints.single().completed)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, ended) }
    }

    @Test
    fun `automatic transition remains complete when event arrives before target snapshot`() =
        runTest {
            val repository = FakeRecentMediaRepository()
            val recorder = recorder(repository)
            val old = snapshot(
                PlayerStatus.PLAYING,
                mediaId = "old-media",
                queueItemId = "old-occurrence",
                positionMs = 90L,
                durationMs = 100L,
            )
            val next = snapshot(
                PlayerStatus.LOADING,
                mediaId = "next-media",
                queueItemId = "next-occurrence",
                positionMs = 0L,
                revision = 1L,
            )

            recorder.bind(EPOCH_ONE, old)
            awaitRecorder { recorder.sampleNow() }
            repository.checkpoints.clear()
            recorder.observeEvent(
                EPOCH_ONE,
                transition(
                    from = "old-occurrence",
                    to = "next-occurrence",
                    reason = ItemTransitionReason.AUTOMATIC,
                ),
            )
            runCurrent()
            recorder.observeSnapshot(EPOCH_ONE, next)
            awaitRecorder { recorder.sampleNow() }

            assertEquals(1, repository.checkpoints.size)
            with(repository.checkpoints.single()) {
                assertEquals(MediaId("old-media"), mediaId)
                assertEquals(Milliseconds(100L), position)
                assertTrue(completed)
            }
            awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, next) }
            assertTrue(
                repository.checkpoints.last { it.mediaId == MediaId("old-media") }.completed,
            )
        }

    @Test
    fun `transition fallback completes with the latest pre-transition duration`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val initial = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 10L,
            durationMs = null,
        )
        val latestSource = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
            revision = 1L,
        )
        val target = snapshot(
            PlayerStatus.LOADING,
            mediaId = "target-media",
            queueItemId = "target-occurrence",
            positionMs = 0L,
            revision = 2L,
        )

        recorder.bind(EPOCH_ONE, initial)
        recorder.observeSnapshot(EPOCH_ONE, latestSource)
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "target-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 1L,
                revision = 2L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        val completed = repository.checkpoints.last { it.mediaId == MediaId("old-media") }
        assertTrue(completed.completed)
        assertEquals(Milliseconds(100L), completed.position)
        assertEquals(Milliseconds(100L), completed.duration)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, target) }
    }

    @Test
    fun `later target state cannot downgrade an already handled completion`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
        )
        val targetPlaying = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "target-media",
            queueItemId = "target-occurrence",
            positionMs = 10L,
            durationMs = 100L,
            revision = 2L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        repository.checkpoints.clear()
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "target-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 1L,
                revision = 1L,
            ),
        )
        recorder.observeEvent(EPOCH_ONE, stateChanged(targetPlaying, sequence = 2L))
        awaitRecorder { recorder.sampleNow() }

        val sourceWrites = repository.checkpoints.filter { it.mediaId == MediaId("old-media") }
        assertEquals(1, sourceWrites.size)
        assertTrue(sourceWrites.single().completed)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, targetPlaying) }
    }

    @Test
    fun `ordered state event recovers a transition skipped by StateFlow`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
        )
        val nextLoading = snapshot(
            PlayerStatus.LOADING,
            mediaId = "next-media",
            queueItemId = "next-occurrence",
            positionMs = 0L,
            revision = 1L,
        )
        val nextPlaying = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "next-media",
            queueItemId = "next-occurrence",
            positionMs = 10L,
            durationMs = 100L,
            revision = 2L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        repository.checkpoints.clear()
        recorder.observeSnapshot(EPOCH_ONE, nextPlaying)
        recorder.observeEvent(
            EPOCH_ONE,
            stateChanged(nextLoading, sequence = 1L),
        )
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "next-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 2L,
            ),
        )
        recorder.observeEvent(EPOCH_ONE, stateChanged(nextPlaying, sequence = 3L))
        awaitRecorder { recorder.sampleNow() }

        val source = repository.checkpoints.last { it.mediaId == MediaId("old-media") }
        with(source) {
            assertEquals(MediaId("old-media"), mediaId)
            assertEquals(Milliseconds(100L), position)
            assertTrue(completed)
        }
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, nextPlaying) }
    }

    @Test
    fun `pause flush ahead of transition events retains the completed source`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
        )
        val targetLoading = snapshot(
            PlayerStatus.LOADING,
            mediaId = "target-media",
            queueItemId = "target-occurrence",
            positionMs = 0L,
            revision = 1L,
        )
        val targetPaused = snapshot(
            PlayerStatus.PAUSED,
            mediaId = "target-media",
            queueItemId = "target-occurrence",
            positionMs = 10L,
            durationMs = 100L,
            revision = 2L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        repository.checkpoints.clear()
        awaitRecorder { recorder.flushPause(EPOCH_ONE, targetPaused) }
        recorder.observeEvent(EPOCH_ONE, stateChanged(targetLoading, sequence = 1L))
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "target-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 2L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        val sourceWrites = repository.checkpoints.filter { it.mediaId == MediaId("old-media") }
        assertEquals(1, sourceWrites.size)
        assertTrue(sourceWrites.single().completed)
        assertEquals(Milliseconds(100L), sourceWrites.single().position)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, targetPaused) }
    }

    @Test
    fun `close drains a transition that follows its state event`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
        )
        val targetLoading = snapshot(
            PlayerStatus.LOADING,
            mediaId = "target-media",
            queueItemId = "target-occurrence",
            positionMs = 0L,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, old)
        recorder.observeEvent(EPOCH_ONE, stateChanged(targetLoading, sequence = 1L))
        val closing = async { recorder.closeAndFlush(EPOCH_ONE, targetLoading) }
        runCurrent()
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "target-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 2L,
            ),
        )
        runCurrent()
        advanceTimeBy(50L)
        runCurrent()
        closing.await()

        assertTrue(repository.checkpoints.last { it.mediaId == MediaId("old-media") }.completed)
    }

    @Test
    fun `repeat one completes old play then clears completion for same media`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(PlayerStatus.PLAYING, positionMs = 95L, durationMs = 100L)
        val replay = snapshot(
            PlayerStatus.LOADING,
            positionMs = 0L,
            durationMs = null,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        repository.checkpoints.clear()
        recorder.observeEvent(
            EPOCH_ONE,
            stateChanged(replay, sequence = 1L),
        )
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = DEFAULT_QUEUE_ID,
                to = DEFAULT_QUEUE_ID,
                reason = ItemTransitionReason.REPEAT_ONE,
                sequence = 2L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        assertEquals(listOf(true, false), repository.checkpoints.map { it.completed })
        assertEquals(listOf(100L, 0L), repository.checkpoints.map { it.position.value })
        assertEquals(listOf(100L, 100L), repository.checkpoints.map { it.duration?.value })
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, replay) }
    }

    @Test
    fun `delayed source transition cannot downgrade an ended target`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(
            PlayerStatus.PLAYING,
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
        )
        val targetEnded = snapshot(
            PlayerStatus.ENDED,
            queueItemId = "target-occurrence",
            positionMs = 100L,
            durationMs = 100L,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        recorder.observeEvent(
            EPOCH_ONE,
            stateChanged(targetEnded, sequence = 1L),
        )
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "target-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 2L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        assertEquals(listOf(false, true), repository.checkpoints.map { it.completed })
        assertTrue(repository.checkpoints.last().completed)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, targetEnded) }
    }

    @Test
    fun `rapid repeat one events retain each same occurrence candidate`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val firstPlaying = snapshot(
            PlayerStatus.PLAYING,
            positionMs = 90L,
            durationMs = 100L,
            revision = 1L,
        )
        val secondLoading = snapshot(
            PlayerStatus.LOADING,
            positionMs = 0L,
            durationMs = null,
            revision = 2L,
        )
        val secondPlaying = snapshot(
            PlayerStatus.PLAYING,
            positionMs = 70L,
            durationMs = 80L,
            revision = 3L,
        )
        val thirdLoading = snapshot(
            PlayerStatus.LOADING,
            positionMs = 0L,
            durationMs = null,
            revision = 4L,
        )

        recorder.bind(EPOCH_ONE, firstPlaying)
        recorder.observeSnapshot(EPOCH_ONE, secondPlaying)
        recorder.observeEvent(EPOCH_ONE, stateChanged(secondLoading, sequence = 1L))
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = DEFAULT_QUEUE_ID,
                to = DEFAULT_QUEUE_ID,
                reason = ItemTransitionReason.REPEAT_ONE,
                sequence = 2L,
                revision = 2L,
            ),
        )
        recorder.observeEvent(EPOCH_ONE, stateChanged(secondPlaying, sequence = 3L))
        recorder.observeEvent(EPOCH_ONE, stateChanged(thirdLoading, sequence = 4L))
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = DEFAULT_QUEUE_ID,
                to = DEFAULT_QUEUE_ID,
                reason = ItemTransitionReason.REPEAT_ONE,
                sequence = 5L,
                revision = 4L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        assertTrue(repository.checkpoints.any { it.completed && it.position == Milliseconds(100L) })
        assertTrue(repository.checkpoints.any { it.completed && it.position == Milliseconds(80L) })
        assertFalse(repository.checkpoints.last().completed)
        assertEquals(Milliseconds.ZERO, repository.checkpoints.last().position)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, thirdLoading) }
    }

    @Test
    fun `skip transition flushes previous occurrence as incomplete`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 40L,
            durationMs = 100L,
        )
        val next = snapshot(
            PlayerStatus.LOADING,
            mediaId = "next-media",
            queueItemId = "next-occurrence",
            positionMs = 0L,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        recorder.observeEvent(
            EPOCH_ONE,
            stateChanged(next, sequence = 1L),
        )
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "next-occurrence",
                reason = ItemTransitionReason.SKIP_NEXT,
                sequence = 2L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        assertEquals(1, repository.checkpoints.size)
        with(repository.checkpoints.single()) {
            assertEquals(MediaId("old-media"), mediaId)
            assertEquals(Milliseconds(40L), position)
            assertFalse(completed)
        }
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, next) }
    }

    @Test
    fun `detached engine observations cannot overwrite replacement progress`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val old = snapshot(PlayerStatus.PLAYING, positionMs = 10L, durationMs = 100L)
        val oldFinal = snapshot(PlayerStatus.PLAYING, positionMs = 20L, durationMs = 100L)
        val replacement = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "replacement-media",
            queueItemId = "replacement-occurrence",
            positionMs = 5L,
            durationMs = 200L,
        )
        val stale = snapshot(PlayerStatus.PAUSED, positionMs = 99L, durationMs = 100L)

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        awaitRecorder { recorder.flushAndDetach(EPOCH_ONE, oldFinal) }
        recorder.bind(EPOCH_TWO, replacement)
        val writesBeforeStaleObservations = repository.checkpoints.size
        recorder.observeSnapshot(EPOCH_ONE, stale)
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "replacement-occurrence",
                to = "replacement-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
            ),
        )
        runCurrent()
        assertEquals(writesBeforeStaleObservations, repository.checkpoints.size)
        awaitRecorder { recorder.sampleNow() }

        assertFalse(repository.checkpoints.any { it.position == Milliseconds(99L) })
        assertEquals(MediaId("replacement-media"), repository.checkpoints.last().mediaId)
        assertEquals(Milliseconds(5L), repository.checkpoints.last().position)
        awaitRecorder { recorder.closeAndFlush(EPOCH_TWO, replacement) }
    }

    @Test
    fun `older revision cannot roll back the active candidate`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val current = snapshot(
            PlayerStatus.PLAYING,
            positionMs = 50L,
            durationMs = 100L,
            revision = 10L,
        )
        val stale = snapshot(
            PlayerStatus.PAUSED,
            positionMs = 5L,
            durationMs = 100L,
            revision = 9L,
        )

        recorder.bind(EPOCH_ONE, current)
        recorder.observeSnapshot(EPOCH_ONE, stale)
        awaitRecorder { recorder.sampleNow() }

        assertEquals(1, repository.checkpoints.size)
        assertEquals(Milliseconds(50L), repository.checkpoints.single().position)
        assertFalse(repository.checkpoints.single().completed)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, current) }
    }

    @Test
    fun `ordered state events retain every source when StateFlow skips intermediates`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val first = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "first-media",
            queueItemId = "first-occurrence",
            positionMs = 90L,
            durationMs = 100L,
            revision = 1L,
        )
        val secondLoading = snapshot(
            PlayerStatus.LOADING,
            mediaId = "second-media",
            queueItemId = "second-occurrence",
            positionMs = 0L,
            revision = 2L,
        )
        val secondPlaying = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "second-media",
            queueItemId = "second-occurrence",
            positionMs = 40L,
            durationMs = 80L,
            revision = 3L,
        )
        val thirdLoading = snapshot(
            PlayerStatus.LOADING,
            mediaId = "third-media",
            queueItemId = "third-occurrence",
            positionMs = 0L,
            revision = 4L,
        )

        recorder.bind(EPOCH_ONE, first)
        recorder.observeSnapshot(EPOCH_ONE, thirdLoading)
        recorder.observeEvent(EPOCH_ONE, stateChanged(secondLoading, sequence = 1L))
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "first-occurrence",
                to = "second-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 2L,
                revision = 2L,
            ),
        )
        recorder.observeEvent(EPOCH_ONE, stateChanged(secondPlaying, sequence = 3L))
        recorder.observeEvent(EPOCH_ONE, stateChanged(thirdLoading, sequence = 4L))
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "second-occurrence",
                to = "third-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
                sequence = 5L,
                revision = 4L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        assertTrue(repository.checkpoints.last { it.mediaId == MediaId("first-media") }.completed)
        assertTrue(repository.checkpoints.last { it.mediaId == MediaId("second-media") }.completed)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, thirdLoading) }
    }

    @Test
    fun `close drains an unsampled final position`() = runTest {
        val repository = FakeRecentMediaRepository()
        val recorder = recorder(repository)
        val initial = snapshot(PlayerStatus.PLAYING, positionMs = 10L, durationMs = 100L)
        val final = snapshot(PlayerStatus.PLAYING, positionMs = 42L, durationMs = 100L)

        recorder.bind(EPOCH_ONE, initial)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, final) }

        assertEquals(1, repository.checkpoints.size)
        assertEquals(Milliseconds(42L), repository.checkpoints.single().position)
        assertFalse(repository.checkpoints.single().completed)
    }

    @Test
    fun `failed write is reported and retried before idempotent close`() = runTest {
        val repository = FakeRecentMediaRepository(failuresBeforeSuccess = 1)
        val failures = mutableListOf<Throwable>()
        val recorder = recorder(repository, failureReporter = failures::add)
        val playing = snapshot(PlayerStatus.PLAYING, positionMs = 25L, durationMs = 100L)

        recorder.bind(EPOCH_ONE, playing)
        awaitRecorder { recorder.sampleNow() }
        awaitRecorder { recorder.sampleNow() }

        assertEquals(1, failures.size)
        assertEquals(1, repository.checkpoints.size)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, playing) }
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, playing) }
        assertEquals(1, repository.checkpoints.size)
    }

    @Test
    fun `failed transition completion is retried during close`() = runTest {
        val repository = FakeRecentMediaRepository(failuresBeforeSuccess = 1)
        val failures = mutableListOf<Throwable>()
        val recorder = recorder(repository, failureReporter = failures::add)
        val old = snapshot(
            PlayerStatus.PLAYING,
            mediaId = "old-media",
            queueItemId = "old-occurrence",
            positionMs = 90L,
            durationMs = 100L,
        )
        val target = snapshot(
            PlayerStatus.LOADING,
            mediaId = "target-media",
            queueItemId = "target-occurrence",
            positionMs = 0L,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, old)
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = "old-occurrence",
                to = "target-occurrence",
                reason = ItemTransitionReason.AUTOMATIC,
            ),
        )
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, target) }

        assertEquals(1, failures.size)
        val completed = repository.checkpoints.last { it.mediaId == MediaId("old-media") }
        assertTrue(completed.completed)
        assertEquals(Milliseconds(100L), completed.position)
    }

    @Test
    fun `failed repeat completion is retried before same media reset`() = runTest {
        val repository = FakeRecentMediaRepository()
        val failures = mutableListOf<Throwable>()
        val recorder = recorder(repository, failureReporter = failures::add)
        val old = snapshot(PlayerStatus.PLAYING, positionMs = 95L, durationMs = 100L)
        val replay = snapshot(
            PlayerStatus.LOADING,
            positionMs = 0L,
            durationMs = null,
            revision = 1L,
        )

        recorder.bind(EPOCH_ONE, old)
        awaitRecorder { recorder.sampleNow() }
        repository.checkpoints.clear()
        repository.failNextWrites()
        recorder.observeEvent(EPOCH_ONE, stateChanged(replay, sequence = 1L))
        recorder.observeEvent(
            EPOCH_ONE,
            transition(
                from = DEFAULT_QUEUE_ID,
                to = DEFAULT_QUEUE_ID,
                reason = ItemTransitionReason.REPEAT_ONE,
                sequence = 2L,
            ),
        )
        awaitRecorder { recorder.sampleNow() }

        assertEquals(1, failures.size)
        assertEquals(listOf(true, false), repository.checkpoints.map { it.completed })
        assertEquals(listOf(100L, 0L), repository.checkpoints.map { it.position.value })
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, replay) }
    }

    @Test
    fun `repository timeout is reported and actor remains available for retry`() = runTest {
        val repository = FakeRecentMediaRepository()
        val failures = mutableListOf<Throwable>()
        val recorder = recorder(
            repository = repository,
            repositoryTimeoutMs = 50L,
            failureReporter = failures::add,
        )
        val playing = snapshot(PlayerStatus.PLAYING, positionMs = 25L, durationMs = 100L)

        repository.blockWrites = true
        recorder.bind(EPOCH_ONE, playing)
        val sampling = async { recorder.sampleNow() }
        runCurrent()
        advanceTimeBy(50L)
        runCurrent()
        sampling.await()

        assertEquals(1, failures.size)
        assertTrue(repository.checkpoints.isEmpty())
        repository.blockWrites = false
        awaitRecorder { recorder.sampleNow() }
        assertEquals(Milliseconds(25L), repository.checkpoints.single().position)
        awaitRecorder { recorder.closeAndFlush(EPOCH_ONE, playing) }
    }

    private fun TestScope.recorder(
        repository: FakeRecentMediaRepository,
        clock: () -> Long = { 1_000L },
        repositoryTimeoutMs: Long = 2_000L,
        failureReporter: (Throwable) -> Unit = {},
    ): PlaybackProgressRecorder = PlaybackProgressRecorder(
        repository = repository,
        clock = clock,
        samplePeriodMs = null,
        repositoryTimeoutMs = repositoryTimeoutMs,
        ioDispatcher = StandardTestDispatcher(testScheduler),
        failureReporter = failureReporter,
    )

    private suspend fun TestScope.awaitRecorder(block: suspend () -> Unit) {
        val action = async { block() }
        runCurrent()
        action.await()
    }

    private fun snapshot(
        status: PlayerStatus,
        mediaId: String = DEFAULT_MEDIA_ID,
        queueItemId: String = DEFAULT_QUEUE_ID,
        positionMs: Long,
        durationMs: Long? = null,
        revision: Long = 0L,
    ): PlaybackSnapshot = PlaybackSnapshot(
        status = status,
        playWhenReady = status == PlayerStatus.PLAYING || status == PlayerStatus.BUFFERING,
        queue = PlaybackQueue.of(
            items = listOf(
                QueueItem(
                    id = QueueItemId(queueItemId),
                    media = MediaItem(
                        id = MediaId(mediaId),
                        source = MediaSource("content://test/$mediaId"),
                    ),
                ),
            ),
            currentIndex = 0,
        ),
        timeline = PlaybackTimeline(
            position = Milliseconds(positionMs),
            duration = durationMs?.let(::Milliseconds),
        ),
        revision = revision,
    )

    private fun transition(
        from: String?,
        to: String,
        reason: ItemTransitionReason,
        sequence: Long = 1L,
        revision: Long = 1L,
    ): PlaybackEvent.ItemTransition = PlaybackEvent.ItemTransition(
        sequence = sequence,
        revision = revision,
        from = from?.let(::QueueItemId),
        to = QueueItemId(to),
        reason = reason,
    )

    private fun stateChanged(
        snapshot: PlaybackSnapshot,
        sequence: Long,
    ): PlaybackEvent.StateChanged = PlaybackEvent.StateChanged(
        sequence = sequence,
        revision = snapshot.revision,
        snapshot = snapshot,
        cause = StateChangeCause.BACKEND_EVENT,
    )

    private class FakeRecentMediaRepository(
        private var failuresBeforeSuccess: Int = 0,
        private val storedCheckpoint: PlaybackCheckpoint? = null,
    ) : RecentMediaRepository {
        val checkpoints = mutableListOf<PlaybackCheckpoint>()
        var blockWrites: Boolean = false

        fun failNextWrites(count: Int = 1) {
            require(count >= 0)
            failuresBeforeSuccess += count
        }

        override fun observeRecentlyOpened(limit: Int): Flow<List<RecentMedia>> = emptyFlow()

        override suspend fun findById(id: MediaId): RecentMedia = RecentMedia(
            record = MediaRecord(
                id = id,
                sourceUri = DurableMediaUri("content://test/${id.value}"),
                addedAt = EpochMilliseconds(0L),
                lastOpenedAt = EpochMilliseconds(0L),
            ),
            checkpoint = storedCheckpoint?.takeIf { it.mediaId == id },
        )

        override suspend fun findBySourceUri(sourceUri: DurableMediaUri): RecentMedia? = null

        override suspend fun remember(registration: MediaRegistration): MediaRecord =
            error("Media registration is outside this recorder test.")

        override suspend fun saveCheckpoint(checkpoint: PlaybackCheckpoint): Boolean {
            if (blockWrites) awaitCancellation()
            if (failuresBeforeSuccess > 0) {
                failuresBeforeSuccess -= 1
                error("Injected checkpoint failure.")
            }
            checkpoints += checkpoint
            return true
        }

        override suspend fun remove(id: MediaId): Boolean = false
    }

    private companion object {
        const val EPOCH_ONE = 1L
        const val EPOCH_TWO = 2L
        const val DEFAULT_MEDIA_ID = "media"
        const val DEFAULT_QUEUE_ID = "occurrence"
    }
}
