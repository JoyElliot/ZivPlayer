// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import kotlinx.coroutines.CompletableDeferred

/**
 * Correlates the payload-free callbacks exposed by the bootstrap MPV wrapper.
 *
 * Only one load may be pending. A replacement first waits for the active file's
 * END_FILE callback, and a new generation is armed only by the following
 * START_FILE callback. This is deliberately narrower than the entry-ID mapping
 * required from the release-grade native wrapper.
 */
internal class LoadGenerationFence {
    private val monitor = Any()
    private var activeGeneration: LoadGeneration? = null
    private var pendingStart: PendingStart? = null
    private var pendingStop: CompletableDeferred<Unit>? = null

    fun activeGeneration(): LoadGeneration? = synchronized(monitor) {
        activeGeneration
    }

    fun beginLoad(generation: LoadGeneration): CompletableDeferred<Unit> = synchronized(monitor) {
        check(activeGeneration == null) { "The active file must end before another load begins." }
        check(pendingStart == null) { "Only one libmpv load may be pending." }
        check(pendingStop == null) { "A replacement stop is still pending." }
        CompletableDeferred<Unit>().also { started ->
            pendingStart = PendingStart(generation, started)
        }
    }

    fun cancelLoad(generation: LoadGeneration) {
        val started = synchronized(monitor) {
            val pending = pendingStart
            if (pending?.generation == generation) {
                pendingStart = null
                pending.started
            } else {
                null
            }.also {
                if (activeGeneration == generation) {
                    activeGeneration = null
                }
            }
        }
        started?.cancel()
    }

    fun beginStop(): CompletableDeferred<Unit>? = synchronized(monitor) {
        if (activeGeneration == null && pendingStart == null) {
            null
        } else {
            check(pendingStop == null) { "A libmpv stop is already pending." }
            CompletableDeferred<Unit>().also { pendingStop = it }
        }
    }

    fun cancelStop(stopped: CompletableDeferred<Unit>) {
        val cancelled = synchronized(monitor) {
            if (pendingStop === stopped) {
                pendingStop = null
                true
            } else {
                false
            }
        }
        if (cancelled) {
            stopped.cancel()
        }
    }

    fun onStartFile(): LoadGeneration? {
        val pending = synchronized(monitor) {
            val value = pendingStart ?: return null
            pendingStart = null
            activeGeneration = value.generation
            value
        }
        pending.started.complete(Unit)
        return pending.generation
    }

    fun onEndFile(): LoadGeneration? {
        val transition = synchronized(monitor) {
            val generation = activeGeneration
            activeGeneration = null
            val abandonedStart = pendingStart?.started
            pendingStart = null
            val stopped = pendingStop
            pendingStop = null
            EndTransition(generation, stopped, abandonedStart)
        }
        transition.abandonedStart?.cancel()
        transition.stopped?.complete(Unit)
        return transition.generation
    }

    fun failCurrent(): LoadGeneration? {
        val transition = synchronized(monitor) {
            val generation = activeGeneration ?: pendingStart?.generation
            val abandonedStart = pendingStart?.started
            val stopped = pendingStop
            activeGeneration = null
            pendingStart = null
            pendingStop = null
            FailedTransition(generation, stopped, abandonedStart)
        }
        transition.abandonedStart?.cancel()
        transition.stopped?.complete(Unit)
        return transition.generation
    }

    fun close() {
        val pending = synchronized(monitor) {
            val value = PendingOperations(pendingStart?.started, pendingStop)
            activeGeneration = null
            pendingStart = null
            pendingStop = null
            value
        }
        pending.started?.cancel()
        pending.stopped?.cancel()
    }

    private data class PendingStart(
        val generation: LoadGeneration,
        val started: CompletableDeferred<Unit>,
    )

    private data class EndTransition(
        val generation: LoadGeneration?,
        val stopped: CompletableDeferred<Unit>?,
        val abandonedStart: CompletableDeferred<Unit>?,
    )

    private data class PendingOperations(
        val started: CompletableDeferred<Unit>?,
        val stopped: CompletableDeferred<Unit>?,
    )

    private data class FailedTransition(
        val generation: LoadGeneration?,
        val stopped: CompletableDeferred<Unit>?,
        val abandonedStart: CompletableDeferred<Unit>?,
    )
}
