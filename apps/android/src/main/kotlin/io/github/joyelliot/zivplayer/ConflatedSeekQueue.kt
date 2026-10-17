// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

internal data class ScrubTarget<T>(val identity: T, val positionMs: Long, val exact: Boolean)

/** Main-thread queue: one RPC in flight, one latest target, bounded preview frequency. */
internal class ConflatedSeekQueue<T>(private val intervalMs: Long = 40L) {
    private var pending: ScrubTarget<T>? = null
    private var sentAt: Long? = null
    var inFlight = false
        private set

    fun offer(target: ScrubTarget<T>) { pending = target }
    fun clearPending() { pending = null }
    fun reset() { pending = null; sentAt = null; inFlight = false }
    fun delayUntilReady(now: Long): Long? {
        val target = pending ?: return null
        if (inFlight) return null
        return if (target.exact || sentAt == null) 0L else (intervalMs - (now - checkNotNull(sentAt))).coerceAtLeast(0L)
    }
    fun take(now: Long): ScrubTarget<T>? {
        if (delayUntilReady(now) != 0L) return null
        return pending?.also { pending = null; sentAt = now; inFlight = true }
    }
    fun complete() { inFlight = false }
}
