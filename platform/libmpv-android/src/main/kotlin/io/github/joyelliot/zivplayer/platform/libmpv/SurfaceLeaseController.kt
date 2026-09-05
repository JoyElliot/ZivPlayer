// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

/**
 * Lock-free state machine whose caller supplies the serialization boundary.
 * Keeping it Android-free lets local unit tests exhaust the stale-lease rules.
 */
internal class SurfaceLeaseController<SurfaceType>(
    private val attachNative: (SurfaceType) -> Unit,
    private val detachNative: () -> Unit,
) {
    private val owner = Any()
    private var nextToken = 1L
    private var currentToken: Long? = null
    private var closed = false

    fun attach(surface: SurfaceType): LibmpvSurfaceLease {
        check(!closed) { "The Surface lease controller is closed." }

        if (currentToken != null) {
            detachNative()
            currentToken = null
        }

        check(nextToken > 0L) { "The Surface lease token space is exhausted." }
        val lease = LibmpvSurfaceLease(owner, nextToken)
        nextToken = if (nextToken == Long.MAX_VALUE) 0L else nextToken + 1L
        currentToken = lease.token

        try {
            attachNative(surface)
        } catch (failure: Throwable) {
            runCatching(detachNative)
                .onSuccess { currentToken = null }
                .exceptionOrNull()
                ?.let(failure::addSuppressed)
            throw failure
        }
        return lease
    }

    fun detach(lease: LibmpvSurfaceLease) {
        if (closed || lease.owner !== owner || currentToken != lease.token) {
            return
        }
        detachNative()
        currentToken = null
    }

    fun owns(lease: LibmpvSurfaceLease): Boolean =
        !closed && lease.owner === owner && lease.token == currentToken

    fun close() {
        if (closed) {
            return
        }
        if (currentToken != null) {
            detachNative()
            currentToken = null
        }
        closed = true
    }

    /**
     * Finalizes lease state after the caller has successfully destroyed the
     * native owner. No native detach is attempted because that owner no longer
     * exists; any prior detach failure has been superseded by destruction.
     */
    fun completeAfterNativeDestroy() {
        currentToken = null
        closed = true
    }
}
