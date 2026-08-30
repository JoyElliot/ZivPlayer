// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.player.runtime.BackendSeekRequest
import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import io.github.joyelliot.zivplayer.core.player.runtime.SeekGeneration

/** Correlates the single native seek allowed by the bootstrap adapter. */
internal class SeekCorrelation {
    private val monitor = Any()
    private var pending: PendingSeek? = null
    private var positionEpoch: SeekGeneration? = null

    fun begin(request: BackendSeekRequest) {
        synchronized(monitor) {
            check(pending == null) { "Only one native seek may be in flight." }
            pending = PendingSeek(request)
        }
    }

    fun armCompletion(generation: LoadGeneration) {
        synchronized(monitor) {
            pending
                ?.takeIf { it.request.generation == generation }
                ?.completionArmed = true
        }
    }

    fun complete(generation: LoadGeneration): BackendSeekRequest? = synchronized(monitor) {
        val value = pending
        if (value == null || value.request.generation != generation || !value.completionArmed) {
            null
        } else {
            pending = null
            positionEpoch = value.request.seekGeneration
            value.request
        }
    }

    fun fail(request: BackendSeekRequest) {
        synchronized(monitor) {
            if (pending?.request === request) {
                pending = null
            }
        }
    }

    fun positionEpoch(generation: LoadGeneration): SeekGeneration? = synchronized(monitor) {
        pending
            ?.takeIf { it.request.generation == generation && it.completionArmed }
            ?.request
            ?.seekGeneration
            ?: positionEpoch
    }

    fun reset() {
        synchronized(monitor) {
            pending = null
            positionEpoch = null
        }
    }

    private data class PendingSeek(
        val request: BackendSeekRequest,
        var completionArmed: Boolean = false,
    )
}
