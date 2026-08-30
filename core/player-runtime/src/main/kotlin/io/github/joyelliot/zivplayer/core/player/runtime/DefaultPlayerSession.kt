// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.player.runtime

import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.model.QueueItemId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import io.github.joyelliot.zivplayer.core.player.CommandResult
import io.github.joyelliot.zivplayer.core.player.ErrorRecovery
import io.github.joyelliot.zivplayer.core.player.ItemTransitionReason
import io.github.joyelliot.zivplayer.core.player.PlaybackEvent
import io.github.joyelliot.zivplayer.core.player.PlaybackQueue
import io.github.joyelliot.zivplayer.core.player.PlaybackSnapshot
import io.github.joyelliot.zivplayer.core.player.PlaybackTimeline
import io.github.joyelliot.zivplayer.core.player.PlayerCapabilities
import io.github.joyelliot.zivplayer.core.player.PlayerCapability
import io.github.joyelliot.zivplayer.core.player.PlayerCommand
import io.github.joyelliot.zivplayer.core.player.PlayerError
import io.github.joyelliot.zivplayer.core.player.PlayerErrorKind
import io.github.joyelliot.zivplayer.core.player.PlayerSession
import io.github.joyelliot.zivplayer.core.player.PlayerStatus
import io.github.joyelliot.zivplayer.core.player.RepeatMode
import io.github.joyelliot.zivplayer.core.player.StateChangeCause
import io.github.joyelliot.zivplayer.core.player.TrackSnapshot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlin.coroutines.cancellation.CancellationException

class DefaultPlayerSession(
    private val backend: PlayerBackend,
    dispatcher: CoroutineDispatcher = Dispatchers.Default,
    eventBufferCapacity: Int = DEFAULT_EVENT_BUFFER_CAPACITY,
    backendOperationTimeoutMillis: Long = DEFAULT_BACKEND_OPERATION_TIMEOUT_MILLIS,
    loadReadinessTimeoutMillis: Long = DEFAULT_LOAD_READINESS_TIMEOUT_MILLIS,
) : PlayerSession {
    private val validatedEventBufferCapacity = eventBufferCapacity.also {
        require(it > 0) { "Event buffer capacity must be positive." }
    }
    private val validatedBackendOperationTimeoutMillis = backendOperationTimeoutMillis.also {
        require(it > 0L) { "Backend operation timeout must be positive." }
    }
    private val validatedLoadReadinessTimeoutMillis = loadReadinessTimeoutMillis.also {
        require(it > 0L) { "Load readiness timeout must be positive." }
    }
    private val lifecycleJob = SupervisorJob()
    private val scope = CoroutineScope(lifecycleJob + dispatcher + CoroutineName("ZivPlayerSession"))
    private val mailbox = Channel<Message>(capacity = COMMAND_BUFFER_CAPACITY)
    private val admission = Mutex()
    private val backendEventLifecycleGate = Mutex()
    private val shutdownComplete = CompletableDeferred<Unit>()

    private val mutableSnapshot = MutableStateFlow(
        PlaybackSnapshot(capabilities = INITIAL_CAPABILITIES),
    )
    override val snapshot = mutableSnapshot.asStateFlow()

    private val mutableEvents = MutableSharedFlow<PlaybackEvent>(
        replay = 0,
        extraBufferCapacity = validatedEventBufferCapacity,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    override val events: Flow<PlaybackEvent> = mutableEvents.asSharedFlow()

    @Volatile
    private var closing = false

    @Volatile
    private var backendEventsTerminated = false

    // The following fields are actor-confined.
    private var nextGenerationValue = 0L
    private var nextSeekGenerationValue = 0L
    private var activeGeneration: LoadGeneration? = null
    private var nextEventSequence = 0L
    private var statusBeforeBuffering: PlayerStatus? = null
    private var inFlightSeek: BackendSeekRequest? = null
    private var queuedSeekPosition: Milliseconds? = null
    private var completedSeekGeneration: SeekGeneration? = null
    private var backendCapabilitiesKnown = false
    private var backendEventsFailed = false
    private var backendResetRequired = false
    private var loadReadinessTimeoutJob: Job? = null

    private val backendEventsJob: Job = scope.launchBackendCollector()
    private val actorJob: Job = scope.launchSessionActor()

    override suspend fun dispatch(command: PlayerCommand): CommandResult {
        val reply = CompletableDeferred<CommandResult>()
        val outcome = admission.withLock {
            when {
                closing -> AdmissionOutcome.CLOSED
                mailbox.trySend(Message.Command(command, reply)).isSuccess -> AdmissionOutcome.ADMITTED
                else -> AdmissionOutcome.FULL
            }
        }

        return when (outcome) {
            AdmissionOutcome.ADMITTED -> reply.await()
            AdmissionOutcome.CLOSED -> closedResult()
            AdmissionOutcome.FULL -> CommandResult.Rejected(
                revision = mutableSnapshot.value.revision,
                error = PlayerError(
                    PlayerErrorKind.COMMAND_QUEUE_FULL,
                    "The player command queue is full.",
                ),
            )
        }
    }

    override suspend fun close() {
        withContext(NonCancellable) {
            val initiateShutdown = admission.withLock {
                if (closing) {
                    false
                } else {
                    closing = true
                    true
                }
            }

            if (initiateShutdown) {
                backendEventsJob.cancelAndJoin()
                mailbox.send(Message.Shutdown)
            }

            shutdownComplete.await()
            actorJob.join()
            lifecycleJob.cancel()
        }
    }

    private fun CoroutineScope.launchSessionActor(): Job =
        launch {
            for (message in mailbox) {
                when (message) {
                    is Message.Command -> {
                        val result = try {
                            handleCommand(message.command)
                        } catch (_: Throwable) {
                            val error = backendOperationError("process the command")
                            enterError(error, StateChangeCause.COMMAND)
                            CommandResult.Failed(mutableSnapshot.value.revision, error)
                        }
                        message.reply.complete(result)
                    }

                    is Message.Backend -> {
                        try {
                            handleBackendEvent(message.event)
                        } catch (_: Throwable) {
                            enterError(backendOperationError("process a playback event"))
                        }
                    }
                    Message.BackendCollectorFailed -> handleBackendCollectorFailure()
                    is Message.LoadReadinessTimedOut -> handleLoadReadinessTimeout(message.generation)
                    Message.Shutdown -> {
                        handleShutdown()
                        break
                    }
                }
            }
        }

    private fun CoroutineScope.launchBackendCollector(): Job =
        launch(start = CoroutineStart.UNDISPATCHED) {
            try {
                backend.events.collect { event ->
                    mailbox.send(Message.Backend(event))
                }
            } catch (cancellation: CancellationException) {
                if (closing) {
                    throw cancellation
                }
            } catch (_: Throwable) {
                // Report below. The event stream is required to stay alive
                // until session shutdown, regardless of how it terminates.
            }

            if (!closing) {
                backendEventLifecycleGate.withLock {
                    backendEventsTerminated = true
                }
                withContext(NonCancellable) {
                    try {
                        mailbox.send(Message.BackendCollectorFailed)
                    } catch (_: Throwable) {
                        // The actor may already have completed shutdown.
                    }
                }
            }
        }

    private suspend fun handleCommand(command: PlayerCommand): CommandResult {
        if (mutableSnapshot.value.status == PlayerStatus.CLOSED) {
            return closedResult()
        }
        if (backendResetRequired &&
            command !is PlayerCommand.SetRepeatMode &&
            command != PlayerCommand.ClearQueue
        ) {
            return reject("The playback session must be closed and recreated after this error.")
        }
        return when (command) {
            is PlayerCommand.SetQueue -> handleSetQueue(command)
            PlayerCommand.Play -> handlePlay()
            PlayerCommand.Pause -> handlePause()
            is PlayerCommand.SeekTo -> handleSeek(command)
            PlayerCommand.Stop -> handleStop()
            PlayerCommand.SkipNext -> handleSkip(forward = true)
            PlayerCommand.SkipPrevious -> handleSkip(forward = false)
            is PlayerCommand.SetRepeatMode -> handleSetRepeatMode(command)
            is PlayerCommand.SetVolume -> handleSetVolume(command)
            is PlayerCommand.SetPlaybackRate -> handleSetPlaybackRate(command)
            is PlayerCommand.SetMuted -> handleSetMuted(command)
            is PlayerCommand.SelectTrack -> handleSelectTrack(command)
            PlayerCommand.ClearQueue -> handleClearQueue()
        }
    }

    private suspend fun handleSetQueue(command: PlayerCommand.SetQueue): CommandResult {
        if (command.items.isEmpty()) {
            return reject("A queue must contain at least one item; use ClearQueue to remove it.")
        }

        val queue = try {
            PlaybackQueue.of(command.items, command.startIndex)
        } catch (_: IllegalArgumentException) {
            return reject("The queue has duplicate IDs or an invalid start index.")
        }

        val previous = mutableSnapshot.value.queue.currentItem?.id
        val error = startLoad(
            queue = queue,
            startPosition = command.startPosition,
            playWhenReady = command.playWhenReady,
            previousItem = previous,
            reason = ItemTransitionReason.QUEUE_REPLACED,
        )
        return error?.let { CommandResult.Failed(mutableSnapshot.value.revision, it) }
            ?: accepted(changed = true)
    }

    private suspend fun handlePlay(): CommandResult {
        val current = mutableSnapshot.value
        if (current.status == PlayerStatus.ERROR) {
            return reject("Resolve the active playback error before playing.")
        }
        val item = current.queue.currentItem ?: return reject("Play requires a current queue item.")

        return when (current.status) {
            PlayerStatus.CLOSED -> closedResult()
            PlayerStatus.ERROR -> error("ERROR was handled before queue validation.")
            PlayerStatus.PLAYING -> accepted(changed = false)
            PlayerStatus.LOADING -> {
                if (current.playWhenReady) {
                    accepted(changed = false)
                } else {
                    publish(current.copy(playWhenReady = true), StateChangeCause.COMMAND)
                    accepted(changed = true)
                }
            }

            PlayerStatus.IDLE -> {
                capabilityRejectionIfKnown(PlayerCapability.PLAY)?.let { return it }
                val error = startLoad(
                    queue = current.queue,
                    startPosition = current.timeline.position,
                    playWhenReady = true,
                    previousItem = item.id,
                    reason = null,
                )
                error?.let { CommandResult.Failed(mutableSnapshot.value.revision, it) }
                    ?: accepted(changed = true)
            }

            PlayerStatus.ENDED -> {
                capabilityRejection(PlayerCapability.PLAY)?.let { return it }
                val error = startLoad(
                    queue = current.queue,
                    startPosition = Milliseconds.ZERO,
                    playWhenReady = true,
                    previousItem = item.id,
                    reason = null,
                )
                error?.let { CommandResult.Failed(mutableSnapshot.value.revision, it) }
                    ?: accepted(changed = true)
            }

            PlayerStatus.READY,
            PlayerStatus.PAUSED,
            PlayerStatus.BUFFERING,
            -> {
                capabilityRejection(PlayerCapability.PLAY)?.let { return it }
                val error = callBackend("start playback") { play() }
                if (error != null) {
                    enterError(error, StateChangeCause.COMMAND)
                    CommandResult.Failed(mutableSnapshot.value.revision, error)
                } else {
                    statusBeforeBuffering = null
                    publish(
                        current.copy(
                            status = PlayerStatus.PLAYING,
                            playWhenReady = true,
                            error = null,
                        ),
                        StateChangeCause.COMMAND,
                    )
                    accepted(changed = true)
                }
            }
        }
    }

    private suspend fun handlePause(): CommandResult {
        val current = mutableSnapshot.value
        return when (current.status) {
            PlayerStatus.CLOSED -> closedResult()
            PlayerStatus.LOADING -> {
                if (!current.playWhenReady) {
                    accepted(changed = false)
                } else {
                    publish(current.copy(playWhenReady = false), StateChangeCause.COMMAND)
                    accepted(changed = true)
                }
            }

            PlayerStatus.PLAYING,
            PlayerStatus.BUFFERING,
            -> {
                capabilityRejection(PlayerCapability.PAUSE)?.let { return it }
                val error = callBackend("pause playback") { pause() }
                if (error != null) {
                    enterError(error, StateChangeCause.COMMAND)
                    CommandResult.Failed(mutableSnapshot.value.revision, error)
                } else {
                    statusBeforeBuffering = null
                    publish(
                        current.copy(status = PlayerStatus.PAUSED, playWhenReady = false),
                        StateChangeCause.COMMAND,
                    )
                    accepted(changed = true)
                }
            }

            else -> accepted(changed = false)
        }
    }

    private suspend fun handleSeek(command: PlayerCommand.SeekTo): CommandResult {
        val current = mutableSnapshot.value
        if (current.queue.currentItem == null) {
            return reject("Seek requires a current queue item.")
        }
        if (current.status !in SEEKABLE_STATES) {
            return reject("Seek is not available in ${current.status} state.")
        }
        capabilityRejection(PlayerCapability.SEEK)?.let { return it }
        if (current.timeline.duration?.let { command.position > it } == true) {
            return reject("Seek position must not exceed the known duration.")
        }
        if (command.position == current.timeline.position) {
            return accepted(changed = false)
        }

        if (current.status == PlayerStatus.ENDED) {
            val error = startLoad(
                queue = current.queue,
                startPosition = command.position,
                playWhenReady = false,
                previousItem = current.queue.currentItem?.id,
                reason = null,
            )
            return error?.let { CommandResult.Failed(mutableSnapshot.value.revision, it) }
                ?: accepted(changed = true)
        }

        if (inFlightSeek != null) {
            queuedSeekPosition = command.position
            publish(
                current.copy(timeline = current.timeline.copy(position = command.position)),
                StateChangeCause.COMMAND,
            )
            return accepted(changed = true)
        }

        val error = startSeek(command.position)
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }

        publish(
            current.copy(
                playWhenReady = current.playWhenReady,
                timeline = current.timeline.copy(position = command.position),
            ),
            StateChangeCause.COMMAND,
        )
        return accepted(changed = true)
    }

    private suspend fun handleStop(): CommandResult {
        val current = mutableSnapshot.value
        if (current.queue.currentItem == null || current.status == PlayerStatus.IDLE) {
            return accepted(changed = false)
        }
        if (current.status == PlayerStatus.ERROR && backendResetRequired) {
            return reject("The playback session must be closed and recreated after this error.")
        }
        if (current.status != PlayerStatus.ERROR) {
            capabilityRejectionIfKnown(PlayerCapability.STOP)?.let { return it }
        }

        val error = callBackend(
            operation = "stop playback",
            failureRecovery = ErrorRecovery.RESET,
        ) { stop() }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }

        activeGeneration = null
        cancelLoadReadinessTimeout()
        statusBeforeBuffering = null
        resetSeekState()
        val stoppedCapabilities = if (backendCapabilitiesKnown) {
            current.capabilities
        } else {
            PlayerCapabilities.NONE
        }
        publish(
            current.copy(
                status = PlayerStatus.IDLE,
                playWhenReady = false,
                timeline = PlaybackTimeline(),
                tracks = TrackSnapshot.EMPTY,
                capabilities = stoppedCapabilities,
                error = null,
            ),
            StateChangeCause.COMMAND,
        )
        return accepted(changed = true)
    }

    private suspend fun handleSkip(forward: Boolean): CommandResult {
        val current = mutableSnapshot.value
        val currentIndex = current.queue.currentIndex
            ?: return reject("Queue navigation requires a current item.")
        val targetIndex = navigationTarget(
            queueSize = current.queue.items.size,
            currentIndex = currentIndex,
            forward = forward,
            repeatMode = current.repeatMode,
        ) ?: return reject(if (forward) "There is no next item." else "There is no previous item.")
        capabilityRejectionIfKnown(
            if (forward) PlayerCapability.SKIP_NEXT else PlayerCapability.SKIP_PREVIOUS,
        )?.let { return it }

        val targetQueue = current.queue.withCurrentIndex(targetIndex)
        val error = startLoad(
            queue = targetQueue,
            startPosition = Milliseconds.ZERO,
            playWhenReady = current.playWhenReady || current.status == PlayerStatus.PLAYING,
            previousItem = current.queue.currentItem?.id,
            reason = if (forward) ItemTransitionReason.SKIP_NEXT else ItemTransitionReason.SKIP_PREVIOUS,
        )
        return error?.let { CommandResult.Failed(mutableSnapshot.value.revision, it) }
            ?: accepted(changed = true)
    }

    private suspend fun handleSetRepeatMode(command: PlayerCommand.SetRepeatMode): CommandResult {
        val current = mutableSnapshot.value
        if (current.repeatMode == command.repeatMode) {
            return accepted(changed = false)
        }
        publish(current.copy(repeatMode = command.repeatMode), StateChangeCause.COMMAND)
        return accepted(changed = true)
    }

    private suspend fun handleSetVolume(command: PlayerCommand.SetVolume): CommandResult {
        val current = mutableSnapshot.value
        if (current.volume == command.volume) {
            return accepted(changed = false)
        }
        capabilityRejection(PlayerCapability.SET_VOLUME)?.let { return it }
        val error = callBackend("set volume") { setVolume(command.volume) }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }
        publish(current.copy(volume = command.volume), StateChangeCause.COMMAND)
        return accepted(changed = true)
    }

    private suspend fun handleSetPlaybackRate(command: PlayerCommand.SetPlaybackRate): CommandResult {
        val current = mutableSnapshot.value
        if (current.playbackRate == command.playbackRate) {
            return accepted(changed = false)
        }
        capabilityRejection(PlayerCapability.SET_RATE)?.let { return it }
        val error = callBackend("set playback rate") { setPlaybackRate(command.playbackRate) }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }
        publish(current.copy(playbackRate = command.playbackRate), StateChangeCause.COMMAND)
        return accepted(changed = true)
    }

    private suspend fun handleSetMuted(command: PlayerCommand.SetMuted): CommandResult {
        val current = mutableSnapshot.value
        if (current.muted == command.muted) {
            return accepted(changed = false)
        }
        capabilityRejection(PlayerCapability.SET_MUTED)?.let { return it }
        val error = callBackend("change mute state") { setMuted(command.muted) }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }
        publish(current.copy(muted = command.muted), StateChangeCause.COMMAND)
        return accepted(changed = true)
    }

    private suspend fun handleSelectTrack(command: PlayerCommand.SelectTrack): CommandResult {
        val current = mutableSnapshot.value
        capabilityRejection(PlayerCapability.SELECT_TRACK)?.let { return it }
        if (command.trackId == null && command.kind != TrackKind.SUBTITLE) {
            return reject("Only subtitle selection may be disabled.")
        }
        if (command.trackId != null) {
            val descriptor = current.tracks.available.firstOrNull { it.id == command.trackId }
                ?: return reject("The selected track does not exist.")
            if (descriptor.kind != command.kind) {
                return reject("The selected track has a different kind.")
            }
        }
        if (current.tracks.selected[command.kind] == command.trackId) {
            return accepted(changed = false)
        }

        val error = callBackend("select a track") {
            selectTrack(command.kind, command.trackId)
        }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }

        val selected = current.tracks.selected.toMutableMap().apply {
            put(command.kind, command.trackId)
        }
        publish(
            current.copy(tracks = TrackSnapshot.of(current.tracks.available, selected)),
            StateChangeCause.COMMAND,
        )
        return accepted(changed = true)
    }

    private suspend fun handleClearQueue(): CommandResult {
        val current = mutableSnapshot.value
        if (current.queue.items.isEmpty()) {
            return accepted(changed = false)
        }

        if (backendResetRequired) {
            publish(
                current.copy(
                    playWhenReady = false,
                    queue = PlaybackQueue.EMPTY,
                    timeline = PlaybackTimeline(),
                    tracks = TrackSnapshot.EMPTY,
                    capabilities = PlayerCapabilities.NONE,
                ),
                StateChangeCause.COMMAND,
            )
            return accepted(changed = true)
        }

        val error = if (activeGeneration != null) {
            callBackend(
                operation = "clear playback",
                failureRecovery = ErrorRecovery.RESET,
            ) { stop() }
        } else {
            null
        }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
            return CommandResult.Failed(mutableSnapshot.value.revision, error)
        }

        activeGeneration = null
        cancelLoadReadinessTimeout()
        statusBeforeBuffering = null
        resetSeekState()
        backendCapabilitiesKnown = false
        publish(PlaybackSnapshot(revision = current.revision), StateChangeCause.COMMAND)
        return accepted(changed = true)
    }

    private suspend fun handleBackendEvent(event: BackendEvent) {
        if (event.generation != activeGeneration) {
            return
        }
        val currentStatus = mutableSnapshot.value.status
        if (currentStatus == PlayerStatus.ERROR ||
            currentStatus == PlayerStatus.ENDED ||
            currentStatus == PlayerStatus.CLOSED
        ) {
            return
        }

        when (event) {
            is BackendEvent.Prepared -> handlePrepared(event)
            is BackendEvent.PositionChanged -> {
                if (inFlightSeek != null || event.seekGeneration != completedSeekGeneration) {
                    return
                }
                val current = mutableSnapshot.value
                publish(
                    current.copy(
                        timeline = PlaybackTimeline.normalized(
                            event.position,
                            event.duration,
                            event.bufferedPosition,
                        ),
                    ),
                    StateChangeCause.BACKEND_EVENT,
                    emitStateEvent = false,
                )
            }

            is BackendEvent.SeekCompleted -> handleSeekCompleted(event)

            is BackendEvent.PlaybackStarted -> {
                val current = mutableSnapshot.value
                if (current.status != PlayerStatus.LOADING &&
                    current.playWhenReady &&
                    current.status != PlayerStatus.PLAYING
                ) {
                    statusBeforeBuffering = null
                    publish(
                        current.copy(status = PlayerStatus.PLAYING, error = null),
                        StateChangeCause.BACKEND_EVENT,
                    )
                }
            }

            is BackendEvent.PlaybackPaused -> {
                val current = mutableSnapshot.value
                if (current.status in setOf(PlayerStatus.PLAYING, PlayerStatus.BUFFERING) &&
                    !current.playWhenReady
                ) {
                    statusBeforeBuffering = null
                    publish(
                        current.copy(status = PlayerStatus.PAUSED, error = null),
                        StateChangeCause.BACKEND_EVENT,
                    )
                }
            }

            is BackendEvent.BufferingChanged -> handleBufferingChanged(event.buffering)
            is BackendEvent.Ended -> handleEnded()
            is BackendEvent.Failure -> enterError(event.error)
        }
    }

    private suspend fun handlePrepared(event: BackendEvent.Prepared) {
        val current = mutableSnapshot.value
        if (current.status != PlayerStatus.LOADING) {
            return
        }
        cancelLoadReadinessTimeout()
        backendCapabilitiesKnown = true
        publish(
            current.copy(
                status = PlayerStatus.READY,
                timeline = PlaybackTimeline.normalized(
                    current.timeline.position,
                    event.duration,
                    current.timeline.bufferedPosition,
                ),
                tracks = event.tracks,
                capabilities = event.capabilities,
                error = null,
            ),
            StateChangeCause.BACKEND_EVENT,
        )

        if (current.playWhenReady) {
            if (PlayerCapability.PLAY !in mutableSnapshot.value.capabilities.available) {
                enterError(requiredCapabilityError(PlayerCapability.PLAY))
                return
            }
            val error = callBackend("start prepared playback") { play() }
            if (error != null) {
                enterError(error)
            } else {
                val prepared = mutableSnapshot.value
                publish(
                    prepared.copy(status = PlayerStatus.PLAYING, playWhenReady = true),
                    StateChangeCause.BACKEND_EVENT,
                )
            }
        }
    }

    private suspend fun handleBufferingChanged(buffering: Boolean) {
        val current = mutableSnapshot.value
        if (current.status !in BUFFERING_STATES) {
            return
        }
        if (buffering) {
            if (current.status != PlayerStatus.BUFFERING) {
                statusBeforeBuffering = current.status
                publish(current.copy(status = PlayerStatus.BUFFERING), StateChangeCause.BACKEND_EVENT)
            }
            return
        }

        if (current.status == PlayerStatus.BUFFERING) {
            val restored = when {
                current.playWhenReady -> PlayerStatus.PLAYING
                statusBeforeBuffering == PlayerStatus.READY -> PlayerStatus.READY
                else -> PlayerStatus.PAUSED
            }
            statusBeforeBuffering = null
            publish(current.copy(status = restored), StateChangeCause.BACKEND_EVENT)
        }
    }

    private suspend fun handleSeekCompleted(event: BackendEvent.SeekCompleted) {
        val pending = inFlightSeek ?: return
        if (event.seekGeneration != pending.seekGeneration) {
            return
        }

        val queuedPosition = queuedSeekPosition
        if (queuedPosition != null && queuedPosition != event.position) {
            inFlightSeek = null
            queuedSeekPosition = null
            val error = startSeek(queuedPosition)
            if (error != null) {
                enterError(error)
            }
            return
        }

        inFlightSeek = null
        queuedSeekPosition = null
        completedSeekGeneration = event.seekGeneration
        val current = mutableSnapshot.value
        publish(
            current.copy(
                timeline = PlaybackTimeline.normalized(
                    event.position,
                    current.timeline.duration,
                    current.timeline.bufferedPosition,
                ),
            ),
            StateChangeCause.BACKEND_EVENT,
            emitStateEvent = false,
        )
    }

    private suspend fun startSeek(position: Milliseconds): PlayerError? {
        val generation = activeGeneration ?: return backendOperationError("seek without active media")
        val request = BackendSeekRequest(
            generation = generation,
            seekGeneration = nextSeekGeneration(),
            position = position,
        )
        inFlightSeek = request
        val error = callBackend("seek") { seekTo(request) }
        if (error != null) {
            inFlightSeek = null
        }
        return error
    }

    private suspend fun handleEnded() {
        val current = mutableSnapshot.value
        if (current.status !in ENDABLE_STATES) {
            return
        }
        val currentIndex = current.queue.currentIndex ?: return
        val targetIndex = when (current.repeatMode) {
            RepeatMode.ONE -> currentIndex
            RepeatMode.OFF -> (currentIndex + 1).takeIf { it < current.queue.items.size }
            RepeatMode.ALL -> (currentIndex + 1) % current.queue.items.size
        }

        if (targetIndex == null || !current.playWhenReady) {
            val endPosition = current.timeline.duration ?: current.timeline.position
            activeGeneration = null
            statusBeforeBuffering = null
            resetSeekState()
            publish(
                current.copy(
                    status = PlayerStatus.ENDED,
                    playWhenReady = false,
                    timeline = current.timeline.copy(position = endPosition),
                ),
                StateChangeCause.BACKEND_EVENT,
            )
            return
        }

        val reason = if (current.repeatMode == RepeatMode.ONE) {
            ItemTransitionReason.REPEAT_ONE
        } else {
            ItemTransitionReason.AUTOMATIC
        }
        startLoad(
            queue = current.queue.withCurrentIndex(targetIndex),
            startPosition = Milliseconds.ZERO,
            playWhenReady = true,
            previousItem = current.queue.currentItem?.id,
            reason = reason,
        )
    }

    private suspend fun handleBackendCollectorFailure() {
        if (backendEventsFailed) {
            return
        }
        backendEventsFailed = true
        backendCapabilitiesKnown = false
        enterError(backendEventStreamError())
    }

    private suspend fun handleLoadReadinessTimeout(generation: LoadGeneration) {
        if (closing || generation != activeGeneration || mutableSnapshot.value.status != PlayerStatus.LOADING) {
            return
        }
        loadReadinessTimeoutJob = null
        enterError(loadReadinessTimeoutError())
    }

    private suspend fun startLoad(
        queue: PlaybackQueue,
        startPosition: Milliseconds,
        playWhenReady: Boolean,
        previousItem: QueueItemId?,
        reason: ItemTransitionReason?,
    ): PlayerError? {
        if (backendResetRequired) {
            return mutableSnapshot.value.error ?: sessionResetRequiredError()
        }
        if (backendEventsFailed || backendEventsTerminated) {
            backendEventsFailed = true
            backendResetRequired = true
            val error = backendEventStreamError()
            if (mutableSnapshot.value.status != PlayerStatus.ERROR) {
                enterError(error, StateChangeCause.COMMAND)
            }
            return error
        }

        cancelLoadReadinessTimeout()
        val generation = nextGeneration()
        activeGeneration = generation
        statusBeforeBuffering = null
        resetSeekState()
        backendCapabilitiesKnown = false
        val current = mutableSnapshot.value
        val next = current.copy(
            status = PlayerStatus.LOADING,
            playWhenReady = playWhenReady,
            queue = queue,
            timeline = PlaybackTimeline(position = startPosition),
            tracks = TrackSnapshot.EMPTY,
            capabilities = PlayerCapabilities.NONE,
            error = null,
        )
        val published = publish(next, StateChangeCause.COMMAND)
        if (reason != null) {
            emitItemTransition(previousItem, queue.currentItem!!.id, reason, published.revision)
        }

        scheduleLoadReadinessTimeout(generation)
        var terminatedBeforeLoad = false
        val error = backendEventLifecycleGate.withLock {
            if (backendEventsTerminated) {
                terminatedBeforeLoad = true
                backendEventStreamError()
            } else {
                callBackendLoad {
                    load(
                        BackendLoadRequest(
                            generation = generation,
                            item = queue.currentItem!!,
                            startPosition = startPosition,
                        ),
                    )
                }
            }
        }
        if (terminatedBeforeLoad) {
            backendEventsFailed = true
            backendResetRequired = true
        }
        if (error != null) {
            enterError(error, StateChangeCause.COMMAND)
        }
        return error
    }

    private suspend fun enterError(
        error: PlayerError,
        cause: StateChangeCause = StateChangeCause.BACKEND_EVENT,
    ) {
        if (error.recovery == ErrorRecovery.RESET) {
            backendResetRequired = true
        }
        activeGeneration = null
        cancelLoadReadinessTimeout()
        statusBeforeBuffering = null
        resetSeekState()
        val current = mutableSnapshot.value
        val failed = publish(
            current.copy(
                status = PlayerStatus.ERROR,
                playWhenReady = false,
                capabilities = if (error.recovery == ErrorRecovery.RESET) {
                    PlayerCapabilities.NONE
                } else {
                    current.capabilities
                },
                error = error,
            ),
            cause,
        )
        emitError(error, failed.revision)
    }

    private suspend fun handleShutdown() {
        cancelLoadReadinessTimeout()
        val closeError = callBackend("close playback") { close() }
        if (closeError != null) {
            emitError(closeError)
        }

        activeGeneration = null
        statusBeforeBuffering = null
        resetSeekState()
        val current = mutableSnapshot.value
        val closed = publish(
            current.copy(
                status = PlayerStatus.CLOSED,
                playWhenReady = false,
                capabilities = PlayerCapabilities.NONE,
                error = null,
            ),
            StateChangeCause.SHUTDOWN,
        )
        mutableEvents.tryEmit(
            PlaybackEvent.SessionClosed(
                sequence = nextSequence(),
                revision = closed.revision,
            ),
        )
        mailbox.close()
        shutdownComplete.complete(Unit)
    }

    private suspend fun publish(
        candidate: PlaybackSnapshot,
        cause: StateChangeCause,
        emitStateEvent: Boolean = true,
    ): PlaybackSnapshot {
        val revised = candidate.copy(
            capabilities = availableCapabilities(candidate),
            revision = mutableSnapshot.value.revision + 1L,
        )
        mutableSnapshot.value = revised
        if (emitStateEvent) {
            mutableEvents.tryEmit(
                PlaybackEvent.StateChanged(
                    sequence = nextSequence(),
                    revision = revised.revision,
                    snapshot = revised,
                    cause = cause,
                ),
            )
        }
        return revised
    }

    private fun availableCapabilities(snapshot: PlaybackSnapshot): PlayerCapabilities {
        if (snapshot.status == PlayerStatus.CLOSED) {
            return PlayerCapabilities.NONE
        }
        val provisionalBackendCapabilities = buildSet {
            if (snapshot.status == PlayerStatus.LOADING) {
                add(PlayerCapability.PLAY)
                add(PlayerCapability.PAUSE)
                add(PlayerCapability.STOP)
            }
            if (snapshot.status == PlayerStatus.IDLE &&
                !backendCapabilitiesKnown &&
                !backendEventsFailed &&
                !backendEventsTerminated &&
                !backendResetRequired &&
                snapshot.queue.currentItem != null
            ) {
                add(PlayerCapability.PLAY)
            }
            if (snapshot.status == PlayerStatus.ERROR &&
                !backendResetRequired &&
                snapshot.queue.currentItem != null
            ) {
                add(PlayerCapability.STOP)
            }
        }
        val supported = snapshot.capabilities.supported + RUNTIME_CAPABILITIES + provisionalBackendCapabilities
        val backendAvailable =
            snapshot.capabilities.backendAvailable + RUNTIME_CAPABILITIES + provisionalBackendCapabilities
        val stateAvailable = buildSet {
            if (snapshot.queue.currentItem != null && snapshot.status != PlayerStatus.CLOSED) {
                if (snapshot.status != PlayerStatus.ERROR) {
                    add(PlayerCapability.PLAY)
                    add(PlayerCapability.STOP)
                } else if (!backendResetRequired) {
                    add(PlayerCapability.STOP)
                }
            }
            if (snapshot.status in setOf(PlayerStatus.PLAYING, PlayerStatus.BUFFERING) ||
                (snapshot.status == PlayerStatus.LOADING && snapshot.playWhenReady)
            ) {
                add(PlayerCapability.PAUSE)
            }
            if (!backendResetRequired && snapshot.status in SEEKABLE_STATES) {
                add(PlayerCapability.SEEK)
            }
            val index = snapshot.queue.currentIndex
            if (!backendResetRequired && index != null && snapshot.queue.items.size > 1) {
                if (index < snapshot.queue.items.lastIndex || snapshot.repeatMode == RepeatMode.ALL) {
                    add(PlayerCapability.SKIP_NEXT)
                }
                if (index > 0 || snapshot.repeatMode == RepeatMode.ALL) {
                    add(PlayerCapability.SKIP_PREVIOUS)
                }
            }
            add(PlayerCapability.SET_REPEAT)
            if (!backendResetRequired) {
                add(PlayerCapability.SET_VOLUME)
                add(PlayerCapability.SET_RATE)
                add(PlayerCapability.SET_MUTED)
                if (snapshot.tracks.available.isNotEmpty()) {
                    add(PlayerCapability.SELECT_TRACK)
                }
            }
        }
        return PlayerCapabilities.of(
            supported = supported,
            backendAvailable = backendAvailable,
            available = stateAvailable.intersect(backendAvailable),
        )
    }

    private suspend fun emitItemTransition(
        from: QueueItemId?,
        to: QueueItemId,
        reason: ItemTransitionReason,
        revision: Long,
    ) {
        mutableEvents.tryEmit(
            PlaybackEvent.ItemTransition(
                sequence = nextSequence(),
                revision = revision,
                from = from,
                to = to,
                reason = reason,
            ),
        )
    }

    private suspend fun emitError(
        error: PlayerError,
        revision: Long = mutableSnapshot.value.revision,
    ) {
        mutableEvents.tryEmit(
            PlaybackEvent.ErrorRaised(
                sequence = nextSequence(),
                revision = revision,
                error = error,
            ),
        )
    }

    private suspend fun callBackend(
        operation: String,
        failureRecovery: ErrorRecovery = ErrorRecovery.RETRY,
        block: suspend PlayerBackend.() -> Unit,
    ): PlayerError? =
        try {
            withTimeout(validatedBackendOperationTimeoutMillis) {
                backend.block()
            }
            null
        } catch (_: TimeoutCancellationException) {
            backendOperationError(operation, failureRecovery)
        } catch (_: Throwable) {
            backendOperationError(operation, failureRecovery)
        }

    private suspend fun callBackendLoad(
        block: suspend PlayerBackend.() -> Unit,
    ): PlayerError? {
        val timeoutMillis = minOf(
            validatedLoadReadinessTimeoutMillis,
            validatedBackendOperationTimeoutMillis,
        )
        return try {
            withTimeout(timeoutMillis) {
                backend.block()
            }
            null
        } catch (_: TimeoutCancellationException) {
            loadOperationTimeoutError()
        } catch (_: Throwable) {
            backendOperationError("load media")
        }
    }

    private fun backendOperationError(
        operation: String,
        recovery: ErrorRecovery = ErrorRecovery.RETRY,
    ): PlayerError =
        PlayerError(
            kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
            message = "The playback backend could not $operation.",
            recovery = recovery,
        )

    private fun backendEventStreamError(): PlayerError =
        PlayerError(
            kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
            message = "The playback backend event stream terminated unexpectedly.",
            recovery = ErrorRecovery.RESET,
        )

    private fun loadReadinessTimeoutError(): PlayerError =
        PlayerError(
            kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
            message = "The playback backend did not prepare the media before the readiness deadline.",
            recovery = ErrorRecovery.RESET,
        )

    private fun loadOperationTimeoutError(): PlayerError =
        PlayerError(
            kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
            message = "The playback backend did not complete the load operation before its deadline.",
            recovery = ErrorRecovery.RESET,
        )

    private fun requiredCapabilityError(capability: PlayerCapability): PlayerError =
        PlayerError(
            kind = PlayerErrorKind.UNSUPPORTED_OPERATION,
            message = "The playback backend did not expose the required $capability capability.",
            recovery = ErrorRecovery.RESET,
        )

    private fun sessionResetRequiredError(): PlayerError =
        PlayerError(
            kind = PlayerErrorKind.BACKEND_OPERATION_FAILED,
            message = "The playback session must be closed and recreated before another load.",
            recovery = ErrorRecovery.RESET,
        )

    private fun scheduleLoadReadinessTimeout(generation: LoadGeneration) {
        loadReadinessTimeoutJob = scope.launch {
            delay(validatedLoadReadinessTimeoutMillis)
            mailbox.send(Message.LoadReadinessTimedOut(generation))
        }
    }

    private fun cancelLoadReadinessTimeout() {
        loadReadinessTimeoutJob?.cancel()
        loadReadinessTimeoutJob = null
    }

    private fun reject(message: String): CommandResult.Rejected =
        CommandResult.Rejected(
            revision = mutableSnapshot.value.revision,
            error = PlayerError(PlayerErrorKind.INVALID_COMMAND, message),
        )

    private fun capabilityRejection(capability: PlayerCapability): CommandResult.Rejected? {
        val capabilities = mutableSnapshot.value.capabilities
        return when {
            capability !in capabilities.supported -> CommandResult.Rejected(
                revision = mutableSnapshot.value.revision,
                error = PlayerError(
                    PlayerErrorKind.UNSUPPORTED_OPERATION,
                    "$capability is not supported by the playback backend.",
                ),
            )

            capability !in capabilities.available -> reject(
                "$capability is not available in the current playback state.",
            )

            else -> null
        }
    }

    private fun capabilityRejectionIfKnown(
        capability: PlayerCapability,
    ): CommandResult.Rejected? =
        if (!backendCapabilitiesKnown) {
            null
        } else {
            capabilityRejection(capability)
        }

    private fun accepted(changed: Boolean): CommandResult.Accepted =
        CommandResult.Accepted(mutableSnapshot.value.revision, changed)

    private fun closedResult(): CommandResult.Rejected =
        CommandResult.Rejected(
            revision = mutableSnapshot.value.revision,
            error = PlayerError(PlayerErrorKind.SESSION_CLOSED, "The player session is closed."),
        )

    private fun navigationTarget(
        queueSize: Int,
        currentIndex: Int,
        forward: Boolean,
        repeatMode: RepeatMode,
    ): Int? {
        val candidate = if (forward) currentIndex + 1 else currentIndex - 1
        if (candidate in 0 until queueSize) {
            return candidate
        }
        return if (repeatMode == RepeatMode.ALL && queueSize > 1) {
            if (forward) 0 else queueSize - 1
        } else {
            null
        }
    }

    private fun nextGeneration(): LoadGeneration {
        nextGenerationValue += 1L
        return LoadGeneration(nextGenerationValue)
    }

    private fun nextSeekGeneration(): SeekGeneration {
        nextSeekGenerationValue += 1L
        return SeekGeneration(nextSeekGenerationValue)
    }

    private fun resetSeekState() {
        inFlightSeek = null
        queuedSeekPosition = null
        completedSeekGeneration = null
    }

    private fun nextSequence(): Long {
        nextEventSequence += 1L
        return nextEventSequence
    }

    private sealed interface Message {
        data class Command(
            val command: PlayerCommand,
            val reply: CompletableDeferred<CommandResult>,
        ) : Message

        data class Backend(val event: BackendEvent) : Message

        data object BackendCollectorFailed : Message

        data class LoadReadinessTimedOut(val generation: LoadGeneration) : Message

        data object Shutdown : Message
    }

    private enum class AdmissionOutcome {
        ADMITTED,
        CLOSED,
        FULL,
    }

    private companion object {
        const val DEFAULT_EVENT_BUFFER_CAPACITY = 64
        const val DEFAULT_BACKEND_OPERATION_TIMEOUT_MILLIS = 10_000L
        const val DEFAULT_LOAD_READINESS_TIMEOUT_MILLIS = 15_000L
        const val COMMAND_BUFFER_CAPACITY = 64

        val SEEKABLE_STATES = setOf(
            PlayerStatus.READY,
            PlayerStatus.PLAYING,
            PlayerStatus.PAUSED,
            PlayerStatus.BUFFERING,
            PlayerStatus.ENDED,
        )

        val BUFFERING_STATES = setOf(
            PlayerStatus.READY,
            PlayerStatus.PLAYING,
            PlayerStatus.PAUSED,
            PlayerStatus.BUFFERING,
        )

        val ENDABLE_STATES = setOf(
            PlayerStatus.PLAYING,
            PlayerStatus.PAUSED,
            PlayerStatus.BUFFERING,
        )

        val RUNTIME_CAPABILITIES = setOf(
            PlayerCapability.SKIP_NEXT,
            PlayerCapability.SKIP_PREVIOUS,
            PlayerCapability.SET_REPEAT,
        )

        val INITIAL_CAPABILITIES = PlayerCapabilities.of(
            supported = RUNTIME_CAPABILITIES,
            backendAvailable = RUNTIME_CAPABILITIES,
            available = setOf(PlayerCapability.SET_REPEAT),
        )
    }
}
