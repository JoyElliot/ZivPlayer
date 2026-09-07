// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

internal data class SpeedPlaybackIdentity(val connection: Long, val mediaId: String, val sequence: Long, val index: Int)

/** Owns a temporary override independently of Compose pointer cancellation and recomposition. */
internal class TemporaryPlaybackSpeed {
    private data class Lease(val token: Long, val identity: SpeedPlaybackIdentity, val originalSpeed: Float)
    private var serial = 0L
    private var lease: Lease? = null
    val activeToken: Long? get() = lease?.token

    fun begin(identity: SpeedPlaybackIdentity, speed: Float): Long {
        val token = ++serial
        lease = Lease(token, identity, speed)
        return token
    }

    fun owns(token: Long, identity: SpeedPlaybackIdentity?): Boolean =
        lease?.let { it.token == token && it.identity == identity } == true

    fun finish(token: Long, identity: SpeedPlaybackIdentity?): Float? {
        val current = lease ?: return null
        if (token != current.token) return null
        lease = null
        return current.originalSpeed.takeIf { current.identity == identity }
    }

    fun invalidate() { lease = null }
}
