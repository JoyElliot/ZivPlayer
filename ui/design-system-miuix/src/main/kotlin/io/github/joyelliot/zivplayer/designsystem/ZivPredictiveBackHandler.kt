// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.activity.compose.PredictiveBackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import kotlin.coroutines.cancellation.CancellationException

/** Bind a page stack to the Activity's system-back stream, including buttons and accessibility back. */
@Composable
fun ZivPredictiveBackHandler(state: ZivPageStackState, enabled: Boolean = true) {
    val active by rememberUpdatedState(enabled)
    DisposableEffect(state, enabled) {
        onDispose { state.cancelBack() }
    }
    PredictiveBackHandler(enabled = enabled && state.canPop) { events ->
        val token = if (active) state.beginBack() else null
        try {
            // Always collect, including stale callbacks after an enabled-state change.
            events.collect { event ->
                if (token != null) {
                    if (active) state.seekBack(token, event.progress) else state.cancelBack(token)
                }
            }
            if (token != null && active) state.completeBack(token)
        } catch (cancelled: CancellationException) {
            if (token != null) state.cancelBack(token)
            throw cancelled
        }
    }
}
