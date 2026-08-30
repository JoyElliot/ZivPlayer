// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.player.ItemTransitionReason
import io.github.joyelliot.zivplayer.core.player.PlaybackEvent
import io.github.joyelliot.zivplayer.core.player.PlaybackSnapshot
import io.github.joyelliot.zivplayer.core.player.PlayerStatus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.selects.select
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Serializes service-owned playback checkpoints away from the Media3 application looper.
 *
 * StateFlow snapshots are the authoritative position source. Ordered StateChanged events advance
 * structural state so a conflated StateFlow cannot erase an intermediate queue transition.
 */
internal class PlaybackProgressRecorder(
    private val repository: RecentMediaRepository,
    private val clock: () -> Long = System::currentTimeMillis,
    private val samplePeriodMs: Long? = DEFAULT_SAMPLE_PERIOD_MS,
    private val repositoryTimeoutMs: Long = DEFAULT_REPOSITORY_TIMEOUT_MS,
    ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val failureReporter: (Throwable) -> Unit = {},
) {
    private val recorderJob = SupervisorJob()
    private val scope = CoroutineScope(recorderJob + ioDispatcher)
    private val messages = Channel<Message>(Channel.UNLIMITED)
    private val lifecycleGate = Mutex()
    private val closing = AtomicBoolean(false)
    private val closeCompletion = CompletableDeferred<Unit>()
    private val actorTermination = CompletableDeferred<Throwable?>()
    private val startLock = Any()

    @Volatile
    private var actorJob: Job? = null
    private var tickerJob: Job? = null

    init {
        require(samplePeriodMs == null || samplePeriodMs > 0L) {
            "Playback progress sample period must be positive."
        }
        require(repositoryTimeoutMs > 0L) {
            "Playback progress repository timeout must be positive."
        }
    }

    fun bind(epoch: Long, snapshot: PlaybackSnapshot) {
        if (closing.get()) return
        ensureStarted()
        messages.trySend(Message.Bind(epoch, snapshot))
    }

    fun observeSnapshot(epoch: Long, snapshot: PlaybackSnapshot) {
        ensureStarted()
        messages.trySend(Message.Snapshot(epoch, snapshot))
    }

    fun observeEvent(epoch: Long, event: PlaybackEvent) {
        ensureStarted()
        messages.trySend(Message.Event(epoch, event))
    }

    suspend fun flushPause(epoch: Long, snapshot: PlaybackSnapshot) {
        request { acknowledgement -> Message.FlushPause(epoch, snapshot, acknowledgement) }
    }

    suspend fun flushStop(epoch: Long, snapshot: PlaybackSnapshot) {
        request { acknowledgement -> Message.FlushStop(epoch, snapshot, acknowledgement) }
    }

    suspend fun flushAndDetach(epoch: Long, snapshot: PlaybackSnapshot) {
        request { acknowledgement -> Message.FlushAndDetach(epoch, snapshot, acknowledgement) }
    }

    internal suspend fun sampleNow() {
        request { acknowledgement -> Message.Tick(acknowledgement) }
    }

    suspend fun closeAndFlush(epoch: Long, snapshot: PlaybackSnapshot) {
        withContext(NonCancellable) {
            val acknowledgement = lifecycleGate.withLock {
                if (!closing.compareAndSet(false, true)) {
                    return@withLock null
                }
                ensureStarted()
                CompletableDeferred<Unit>().also { completion ->
                    messages.send(Message.Close(epoch, snapshot, completion))
                }
            }

            if (acknowledgement == null) {
                closeCompletion.await()
                return@withContext
            }

            var closeFailure: Throwable? = null
            try {
                awaitAcknowledgement(acknowledgement)
                tickerJob?.cancelAndJoin()
                actorJob?.join()
                actorTermination.await()?.let { throw it }
            } catch (failure: Throwable) {
                closeFailure = failure
                throw failure
            } finally {
                messages.close()
                scope.cancel()
                closeFailure?.let(closeCompletion::completeExceptionally)
                    ?: closeCompletion.complete(Unit)
            }
        }
    }

    private suspend fun request(message: (CompletableDeferred<Unit>) -> Message) {
        ensureStarted()
        lifecycleGate.withLock {
            if (closing.get()) {
                closeCompletion.await()
                return
            }
            val acknowledgement = CompletableDeferred<Unit>()
            messages.send(message(acknowledgement))
            awaitAcknowledgement(acknowledgement)
        }
    }

    private suspend fun awaitAcknowledgement(acknowledgement: CompletableDeferred<Unit>) {
        select {
            acknowledgement.onAwait { }
            actorTermination.onAwait { failure ->
                if (failure != null) throw failure
                acknowledgement.await()
            }
        }
    }

    private fun ensureStarted() {
        if (actorJob != null) return
        synchronized(startLock) {
            if (actorJob != null) return
            actorJob = scope.launch(start = CoroutineStart.LAZY) { runActor() }.also { job ->
                job.invokeOnCompletion { failure -> actorTermination.complete(failure) }
            }
            tickerJob = samplePeriodMs?.let { period ->
                scope.launch(start = CoroutineStart.LAZY) {
                    while (isActive) {
                        delay(period)
                        messages.send(Message.Tick())
                    }
                }
            }
            actorJob?.start()
            tickerJob?.start()
        }
    }

    private suspend fun runActor() {
        val state = RecorderState()
        for (message in messages) {
            var shouldClose = false
            try {
                when (message) {
                    is Message.Bind -> state.bind(message.epoch, message.snapshot)

                    is Message.Snapshot ->
                        state.observePositionSnapshot(message.epoch, message.snapshot)

                    is Message.Event -> state.observeEvent(message.epoch, message.event)

                    is Message.Tick -> state.sampleActive()

                    is Message.FlushPause ->
                        state.flushPause(message.epoch, message.snapshot)

                    is Message.FlushStop ->
                        state.flushStop(message.epoch, message.snapshot)

                    is Message.FlushAndDetach ->
                        if (state.prepareFinalFlush(message.epoch, message.snapshot)) {
                            drainFinalObservations(state)
                            state.finishFinalFlush()
                        }

                    is Message.Close -> {
                        if (state.prepareFinalFlush(message.epoch, message.snapshot)) {
                            drainFinalObservations(state)
                            state.finishFinalFlush()
                        } else {
                            state.discardSession()
                        }
                        shouldClose = true
                    }
                }
            } catch (failure: Throwable) {
                rethrowIfRecorderCancelled(failure)
                reportFailure(failure)
                shouldClose = shouldClose || message is Message.Close
            } finally {
                message.acknowledgement?.complete(Unit)
            }
            if (shouldClose) return
        }
    }

    private suspend fun drainFinalObservations(state: RecorderState) {
        // StateChanged and ItemTransition are emitted back-to-back but enter this channel from
        // another coroutine. Bound both idle time and message count so the pair can arrive
        // without allowing a noisy position stream to hold service shutdown indefinitely.
        var drainedMessages = 0
        while (drainedMessages < MAX_FINAL_DRAIN_MESSAGES) {
            val message = withTimeoutOrNull(FINAL_EVENT_QUIET_PERIOD_MS) {
                messages.receive()
            } ?: return
            drainedMessages += 1
            try {
                when (message) {
                    is Message.Bind -> state.bind(message.epoch, message.snapshot)
                    is Message.Snapshot ->
                        state.observePositionSnapshot(message.epoch, message.snapshot)

                    is Message.Event -> state.observeEvent(message.epoch, message.event)
                    is Message.Tick -> state.sampleActive()
                    is Message.FlushPause -> state.flushPause(message.epoch, message.snapshot)
                    is Message.FlushStop -> state.flushStop(message.epoch, message.snapshot)
                    is Message.FlushAndDetach,
                    is Message.Close,
                    -> reportFailure(
                        IllegalStateException("Nested final playback-progress flush."),
                    )
                }
            } catch (failure: Throwable) {
                rethrowIfRecorderCancelled(failure)
                reportFailure(failure)
            } finally {
                message.acknowledgement?.complete(Unit)
            }
        }
    }

    private fun reportFailure(failure: Throwable) {
        runCatching { failureReporter(failure) }
    }

    private suspend fun rethrowIfRecorderCancelled(failure: Throwable) {
        if (failure is CancellationException) currentCoroutineContext().ensureActive()
    }

    private inner class RecorderState {
        private var activeEpoch: Long? = null
        private var current: Candidate? = null
        private var eventCurrent: Candidate? = null
        private val latestPositionSnapshots = mutableMapOf<QueueItemId, PlaybackSnapshot>()
        private val transitionCandidates = mutableMapOf<TransitionKey, Candidate>()
        private val handledTransitions = mutableListOf<HandledTransition>()
        private var pendingCompletionReset: CompletionReset? = null
        private val knownDurations = mutableMapOf<MediaId, Milliseconds>()
        private val lastPersisted = mutableMapOf<MediaId, CheckpointPayload>()
        private val pendingWrites = linkedMapOf<MediaId, MutableList<PendingWrite>>()
        private val untrackedMediaIds = mutableSetOf<MediaId>()
        private val timestampBaselinesLoaded = mutableSetOf<MediaId>()
        private var lastTimestamp = -1L
        private var lastRevision = -1L
        private var lastEventRevision = -1L
        private var lastEventSequence = -1L

        suspend fun bind(epoch: Long, snapshot: PlaybackSnapshot) {
            activeEpoch = epoch
            clearSessionState()
            observeAuthoritativeSnapshot(epoch, snapshot)
            eventCurrent = current
            lastEventRevision = snapshot.revision
        }

        fun observePositionSnapshot(epoch: Long, snapshot: PlaybackSnapshot) {
            if (epoch != activeEpoch) return
            val item = snapshot.queue.currentItem ?: return
            val latest = latestPositionSnapshots[item.id]
            if (latest != null && snapshot.revision < latest.revision) return

            latestPositionSnapshots[item.id] = snapshot
            snapshot.timeline.duration?.let { knownDurations[item.media.id] = it }
        }

        suspend fun observeAuthoritativeSnapshot(epoch: Long, snapshot: PlaybackSnapshot) {
            if (epoch != activeEpoch || snapshot.revision < lastRevision) return
            lastRevision = snapshot.revision
            observePositionSnapshot(epoch, snapshot)
            val previous = current
            val next = candidateFrom(snapshot, previous)
            if (next == null) {
                current = null
                return
            }

            current = next
            pendingCompletionReset?.takeIf {
                it.to == next.queueItemId && next.revision >= it.targetRevision
            }?.let { pending ->
                pendingCompletionReset = null
                if (pending.mediaId == next.mediaId && next.status != PlayerStatus.ENDED) {
                    persist(next, completed = false)
                }
            }

            when (next.status) {
                PlayerStatus.PAUSED -> persist(next, completed = false)
                PlayerStatus.ENDED -> persist(next, completed = true)
                else -> Unit
            }
        }

        suspend fun observeEvent(epoch: Long, event: PlaybackEvent) {
            if (epoch != activeEpoch || event.sequence <= lastEventSequence) return
            lastEventSequence = event.sequence
            if (event is PlaybackEvent.StateChanged) {
                observeOrderedStateSnapshot(epoch, event.snapshot)
                return
            }
            if (event !is PlaybackEvent.ItemTransition) return
            val from = event.from ?: return
            val transitionKey = TransitionKey(from, event.revision)
            val cached = transitionCandidates.remove(transitionKey)
            val source = cached ?: eventCurrent?.takeIf {
                it.queueItemId == from && it.revision < event.revision
            } ?: current?.takeIf {
                it.queueItemId == from && it.revision < event.revision
            } ?: return
            val completed = event.reason == ItemTransitionReason.AUTOMATIC ||
                event.reason == ItemTransitionReason.REPEAT_ONE ||
                source.status == PlayerStatus.ENDED
            val previous = source.withLatestPosition(beforeRevision = event.revision)

            persist(previous, completed)
            if (cached == null) {
                handledTransitions += HandledTransition(
                    from = from,
                    sourceRevision = source.revision,
                )
                if (handledTransitions.size > MAX_HANDLED_TRANSITIONS) {
                    handledTransitions.removeAt(0)
                }
            }

            if (completed) {
                val active = current
                val sameTargetMedia = active?.queueItemId == event.to &&
                    active.mediaId == previous.mediaId
                if (sameTargetMedia && active.status == PlayerStatus.ENDED) {
                    pendingCompletionReset = null
                } else if (
                    cached != null &&
                    active?.queueItemId == event.to &&
                    active.mediaId == previous.mediaId
                ) {
                    persist(active, completed = false)
                } else {
                    pendingCompletionReset = CompletionReset(
                        to = event.to,
                        mediaId = previous.mediaId,
                        targetRevision = event.revision,
                    )
                }

                if (
                    cached == null &&
                    active?.queueItemId == from &&
                    active.revision < event.revision
                ) {
                    current = active.copy(status = PlayerStatus.ENDED)
                }
            }
        }

        suspend fun sampleActive() {
            retryPendingWrites()
            current?.takeIf {
                it.status == PlayerStatus.PLAYING || it.status == PlayerStatus.BUFFERING
            }?.let { persist(it.withLatestPosition(), completed = false) }
        }

        suspend fun flushPause(epoch: Long, snapshot: PlaybackSnapshot) {
            observeAuthoritativeSnapshot(epoch, snapshot)
            if (epoch == activeEpoch) {
                retryPendingWrites()
                current?.withLatestPosition()?.let { candidate ->
                    persist(candidate, completed = candidate.status == PlayerStatus.ENDED)
                }
            }
        }

        suspend fun flushStop(epoch: Long, snapshot: PlaybackSnapshot) {
            observeAuthoritativeSnapshot(epoch, snapshot)
            if (epoch == activeEpoch) {
                retryPendingWrites()
                current?.withLatestPosition()?.let { candidate ->
                    persist(candidate.copy(position = Milliseconds.ZERO), completed = false)
                }
            }
        }

        suspend fun prepareFinalFlush(epoch: Long, snapshot: PlaybackSnapshot): Boolean {
            observeAuthoritativeSnapshot(epoch, snapshot)
            return epoch == activeEpoch
        }

        suspend fun finishFinalFlush() {
            retryPendingWrites()
            persistUnresolvedEventSource()
            current?.withLatestPosition()?.let { candidate ->
                persist(candidate, completed = candidate.status == PlayerStatus.ENDED)
            }
            retryPendingWrites()
            activeEpoch = null
            clearSessionState()
        }

        fun discardSession() {
            activeEpoch = null
            clearSessionState()
        }

        private fun clearSessionState() {
            current = null
            eventCurrent = null
            latestPositionSnapshots.clear()
            transitionCandidates.clear()
            handledTransitions.clear()
            pendingCompletionReset = null
            lastRevision = -1L
            lastEventRevision = -1L
            lastEventSequence = -1L
        }

        private suspend fun persistUnresolvedEventSource() {
            val source = eventCurrent ?: return
            val active = current
            if (handledTransitions.any { it.matchesSource(source) }) {
                return
            }
            if (
                active != null &&
                (source.queueItemId == active.queueItemId || source.mediaId == active.mediaId)
            ) {
                return
            }
            persist(
                candidate = source.withLatestPosition(beforeRevision = active?.revision),
                completed = source.status == PlayerStatus.ENDED,
            )
        }

        private suspend fun observeOrderedStateSnapshot(
            epoch: Long,
            snapshot: PlaybackSnapshot,
        ) {
            if (epoch != activeEpoch || snapshot.revision <= lastEventRevision) return
            lastEventRevision = snapshot.revision
            observePositionSnapshot(epoch, snapshot)
            val previous = eventCurrent
            val next = candidateFrom(snapshot, previous)
            if (next == null) {
                eventCurrent = null
                if (snapshot.revision >= lastRevision) {
                    observeAuthoritativeSnapshot(epoch, snapshot)
                }
                return
            }

            if (previous != null && previous.queueItemId != next.queueItemId) {
                val transitionKey = TransitionKey(previous.queueItemId, next.revision)
                val handled = handledTransitions.firstOrNull { it.matchesSource(previous) }
                if (handled != null) {
                    handledTransitions.remove(handled)
                } else {
                    val source = previous.withLatestPosition(beforeRevision = next.revision)
                    transitionCandidates[transitionKey] = source
                    persist(source, completed = source.status == PlayerStatus.ENDED)
                }
            } else if (
                previous != null &&
                previous.queueItemId == next.queueItemId &&
                next.status == PlayerStatus.LOADING &&
                next.revision != previous.revision
            ) {
                val transitionKey = TransitionKey(previous.queueItemId, next.revision)
                val handled = handledTransitions.firstOrNull { it.matchesSource(previous) }
                if (handled != null) {
                    handledTransitions.remove(handled)
                } else {
                    transitionCandidates[transitionKey] =
                        previous.withLatestPosition(beforeRevision = next.revision)
                }
            }

            eventCurrent = next
            if (
                previous?.queueItemId == next.queueItemId &&
                previous.status == PlayerStatus.ENDED &&
                next.status == PlayerStatus.LOADING
            ) {
                transitionCandidates.remove(TransitionKey(next.queueItemId, next.revision))
                persist(next, completed = false)
            }

            if (snapshot.revision >= lastRevision) {
                observeAuthoritativeSnapshot(epoch, snapshot)
            }
        }

        private fun candidateFrom(
            snapshot: PlaybackSnapshot,
            previous: Candidate?,
        ): Candidate? {
            val item = snapshot.queue.currentItem ?: return null
            snapshot.timeline.duration?.let { knownDurations[item.media.id] = it }
            val carriedDuration = when {
                previous?.queueItemId == item.id && previous.mediaId == item.media.id ->
                    previous.duration

                else -> knownDurations[item.media.id]
            }
            val duration = (snapshot.timeline.duration ?: carriedDuration)
                ?.takeIf { snapshot.timeline.position <= it }
            val sameItem = previous?.queueItemId == item.id && previous.mediaId == item.media.id
            val previousStatus = previous?.status
            val status = when {
                snapshot.status == PlayerStatus.CLOSED && sameItem -> checkNotNull(previousStatus)
                snapshot.status == PlayerStatus.ERROR &&
                    sameItem &&
                    previousStatus == PlayerStatus.ENDED -> PlayerStatus.ENDED

                else -> snapshot.status
            }
            return Candidate(
                queueItemId = item.id,
                mediaId = item.media.id,
                position = snapshot.timeline.position,
                duration = duration,
                status = status,
                revision = snapshot.revision,
            )
        }

        private fun Candidate.withLatestPosition(beforeRevision: Long? = null): Candidate {
            val latest = latestPositionSnapshots[queueItemId] ?: return this
            val item = latest.queue.currentItem ?: return this
            if (
                item.id != queueItemId ||
                item.media.id != mediaId ||
                latest.status != status ||
                latest.revision < revision ||
                (beforeRevision != null && latest.revision >= beforeRevision)
            ) {
                return this
            }

            val duration = (latest.timeline.duration ?: duration ?: knownDurations[mediaId])
                ?.takeIf { latest.timeline.position <= it }
            return copy(
                position = latest.timeline.position,
                duration = duration,
            )
        }

        private suspend fun persist(candidate: Candidate, completed: Boolean) {
            if (candidate.mediaId in untrackedMediaIds) {
                pendingWrites.remove(candidate.mediaId)
                return
            }

            val write = PendingWrite(candidate, completed)
            if (!pendingWrites[candidate.mediaId].isNullOrEmpty()) {
                enqueuePendingWrite(write)
                return
            }

            if (!attemptPersist(write)) enqueuePendingWrite(write)
        }

        private suspend fun attemptPersist(write: PendingWrite): Boolean {
            val candidate = write.candidate
            val completed = write.completed
            if (candidate.mediaId in untrackedMediaIds) return true
            if (!ensureTimestampBaseline(candidate.mediaId)) {
                return candidate.mediaId in untrackedMediaIds
            }

            val position = if (completed) candidate.duration ?: candidate.position else candidate.position
            val payload = CheckpointPayload(position, candidate.duration, completed)
            if (lastPersisted[candidate.mediaId] == payload) {
                return true
            }

            val timestamp = nextTimestamp()
            return try {
                val stored = withTimeout(repositoryTimeoutMs) {
                    repository.saveCheckpoint(
                        PlaybackCheckpoint(
                            mediaId = candidate.mediaId,
                            position = position,
                            duration = candidate.duration,
                            completed = completed,
                            updatedAt = EpochMilliseconds(timestamp),
                        ),
                    )
                }
                if (stored) {
                    lastPersisted[candidate.mediaId] = payload
                    true
                } else {
                    untrackedMediaIds += candidate.mediaId
                    true
                }
            } catch (failure: Throwable) {
                rethrowIfRecorderCancelled(failure)
                reportFailure(failure)
                false
            }
        }

        private fun enqueuePendingWrite(write: PendingWrite) {
            val mediaId = write.candidate.mediaId
            if (mediaId in untrackedMediaIds) {
                pendingWrites.remove(mediaId)
                return
            }

            val queue = pendingWrites.getOrPut(mediaId) { mutableListOf() }
            val lastIndex = queue.lastIndex
            if (lastIndex >= 0 && queue[lastIndex].completed == write.completed) {
                queue[lastIndex] = write
            } else {
                queue += write
            }
        }

        private suspend fun retryPendingWrites() {
            pendingWrites.keys.toList().forEach { mediaId ->
                val queue = pendingWrites[mediaId] ?: return@forEach
                while (queue.isNotEmpty()) {
                    if (!attemptPersist(queue.first())) break
                    queue.removeAt(0)
                }
                if (queue.isEmpty() || mediaId in untrackedMediaIds) {
                    pendingWrites.remove(mediaId)
                }
            }
        }

        private suspend fun ensureTimestampBaseline(mediaId: MediaId): Boolean {
            if (mediaId in timestampBaselinesLoaded) return true
            return try {
                val stored = withTimeout(repositoryTimeoutMs) {
                    repository.findById(mediaId)
                }
                if (stored == null) {
                    untrackedMediaIds += mediaId
                    false
                } else {
                    stored.checkpoint?.let { checkpoint ->
                        lastTimestamp = maxOf(lastTimestamp, checkpoint.updatedAt.value)
                        lastPersisted[mediaId] = CheckpointPayload(
                            position = checkpoint.position,
                            duration = checkpoint.duration,
                            completed = checkpoint.completed,
                        )
                    }
                    timestampBaselinesLoaded += mediaId
                    true
                }
            } catch (failure: Throwable) {
                rethrowIfRecorderCancelled(failure)
                reportFailure(failure)
                false
            }
        }

        private fun nextTimestamp(): Long {
            check(lastTimestamp != Long.MAX_VALUE) { "Playback checkpoint timestamp overflow." }
            val minimum = lastTimestamp + 1L
            val next = maxOf(clock().coerceAtLeast(0L), minimum)
            lastTimestamp = next
            return next
        }
    }

    private sealed interface Message {
        val acknowledgement: CompletableDeferred<Unit>?

        data class Bind(
            val epoch: Long,
            val snapshot: PlaybackSnapshot,
        ) : Message {
            override val acknowledgement: CompletableDeferred<Unit>? = null
        }

        data class Snapshot(
            val epoch: Long,
            val snapshot: PlaybackSnapshot,
        ) : Message {
            override val acknowledgement: CompletableDeferred<Unit>? = null
        }

        data class Event(
            val epoch: Long,
            val event: PlaybackEvent,
        ) : Message {
            override val acknowledgement: CompletableDeferred<Unit>? = null
        }

        data class Tick(
            override val acknowledgement: CompletableDeferred<Unit>? = null,
        ) : Message

        data class FlushPause(
            val epoch: Long,
            val snapshot: PlaybackSnapshot,
            override val acknowledgement: CompletableDeferred<Unit>,
        ) : Message

        data class FlushStop(
            val epoch: Long,
            val snapshot: PlaybackSnapshot,
            override val acknowledgement: CompletableDeferred<Unit>,
        ) : Message

        data class FlushAndDetach(
            val epoch: Long,
            val snapshot: PlaybackSnapshot,
            override val acknowledgement: CompletableDeferred<Unit>,
        ) : Message

        data class Close(
            val epoch: Long,
            val snapshot: PlaybackSnapshot,
            override val acknowledgement: CompletableDeferred<Unit>,
        ) : Message
    }

    private data class Candidate(
        val queueItemId: QueueItemId,
        val mediaId: MediaId,
        val position: Milliseconds,
        val duration: Milliseconds?,
        val status: PlayerStatus,
        val revision: Long,
    )

    private data class TransitionKey(
        val from: QueueItemId,
        val targetRevision: Long,
    )

    private data class HandledTransition(
        val from: QueueItemId,
        val sourceRevision: Long,
    ) {
        fun matchesSource(source: Candidate): Boolean =
            source.queueItemId == from && source.revision == sourceRevision
    }

    private data class CheckpointPayload(
        val position: Milliseconds,
        val duration: Milliseconds?,
        val completed: Boolean,
    )

    private data class PendingWrite(
        val candidate: Candidate,
        val completed: Boolean,
    )

    private data class CompletionReset(
        val to: QueueItemId,
        val mediaId: MediaId,
        val targetRevision: Long,
    )

    private companion object {
        const val DEFAULT_SAMPLE_PERIOD_MS = 1_000L
        const val DEFAULT_REPOSITORY_TIMEOUT_MS = 2_000L
        const val FINAL_EVENT_QUIET_PERIOD_MS = 50L
        const val MAX_FINAL_DRAIN_MESSAGES = 64
        const val MAX_HANDLED_TRANSITIONS = 64
    }
}
