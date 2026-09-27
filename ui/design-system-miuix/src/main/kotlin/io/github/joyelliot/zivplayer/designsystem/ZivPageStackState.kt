// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

data class ZivPageEntry(val id: Int, val route: String)

/** Route ownership is independent of rendering. Removed pages live until their exit finishes. */
@Stable
class ZivPageStackState internal constructor(
    private val scope: CoroutineScope,
    initialEntries: List<ZivPageEntry> = emptyList(),
    private var nextId: Int = 0,
) {
    var entries by mutableStateOf(initialEntries)
        private set
    var coverage by mutableFloatStateOf(1f)
        private set
    var isTransitionRunning by mutableStateOf(false)
        private set
    private var popping = false
    private var generation = 0L
    private var motion: Job? = null
    private var backToken: Long? = null
    private var backStart = 1f
    val canPop: Boolean get() = entries.isNotEmpty()
    val currentRoute: String? get() = entries.lastOrNull()?.route

    fun push(route: String): Boolean {
        if (isTransitionRunning) return false
        require(route.isNotBlank())
        entries = entries + ZivPageEntry(nextId++, route)
        coverage = 0f // Same-frame handoff: never draw the new page at full coverage first.
        settle(1f, 320)
        return true
    }

    fun pop(): Boolean {
        if (!canPop || popping) return false
        backToken = null
        popping = true
        settle(0f, (320 * coverage).toInt().coerceAtLeast(120)) {
            entries = entries.dropLast(1)
            coverage = 1f
            popping = false
        }
        return true
    }

    internal fun beginBack(): Long? {
        if (!canPop || popping) return null
        motion?.cancel()
        val token = ++generation
        backToken = token
        backStart = coverage
        isTransitionRunning = true
        return token
    }

    internal fun seekBack(token: Long, progress: Float) {
        if (backToken == token && progress.isFinite()) coverage = backStart * (1f - progress.coerceIn(0f, 1f))
    }

    internal fun completeBack(token: Long) {
        if (backToken == token) pop()
    }

    internal fun cancelBack(token: Long? = backToken) {
        if (token != null && backToken == token) {
            backToken = null
            settle(1f, 160)
        }
    }

    private fun settle(target: Float, duration: Int, finished: () -> Unit = {}) {
        val token = ++generation
        motion?.cancel()
        isTransitionRunning = true
        motion = scope.launch {
            animate(coverage, target, animationSpec = tween(duration)) { value, _ ->
                if (generation == token) coverage = value
            }
            if (generation == token) {
                finished()
                isTransitionRunning = false
            }
        }
    }

    internal fun save(): List<Any> = buildList {
        add(nextId)
        // A committed back is already a navigation decision, even if its visual exit is unfinished.
        (if (popping) entries.dropLast(1) else entries).forEach { add(it.id); add(it.route) }
    }
}

@Composable
fun rememberZivPageStackState(): ZivPageStackState {
    val scope = rememberCoroutineScope()
    return rememberSaveable(saver = listSaver(
        save = { it.save() },
        restore = { saved ->
            ZivPageStackState(scope, saved.drop(1).chunked(2).map { ZivPageEntry(it[0] as Int, it[1] as String) }, saved[0] as Int)
        },
    )) { ZivPageStackState(scope) }
}
