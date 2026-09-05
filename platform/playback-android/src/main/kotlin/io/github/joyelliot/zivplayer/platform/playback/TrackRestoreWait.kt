// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.playback

import kotlinx.coroutines.Deferred
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.selects.select

/** A cancelled restoration must release its Play waiter even while loading remains pending. */
internal suspend fun awaitPreparationOrRestoreCancellation(
    restoration: Deferred<Unit>,
    awaitPrepared: suspend () -> Unit,
): Boolean = coroutineScope {
    val prepared = async { awaitPrepared() }
    try {
        select {
            restoration.onAwait { false }
            prepared.onAwait { true }
        }
    } finally {
        prepared.cancel()
    }
}
