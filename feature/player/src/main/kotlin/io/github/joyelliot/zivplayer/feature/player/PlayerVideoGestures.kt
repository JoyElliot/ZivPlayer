// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.PointerInputChange
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs

private enum class GestureStart { TAP, SEEK, HOLD, CANCEL }

/** The video owns one recognizer. Controls are siblings above this layer and win hit testing. */
@Composable
internal fun PlayerVideoGestures(
    state: PlayerUiState,
    enabled: Boolean,
    onToggleControls: () -> Unit,
    onPlayPause: () -> Unit,
    onSeekTo: (Long) -> Unit,
    onBeginTemporarySpeed: () -> Long?,
    onTemporarySpeedChange: (Long, Float) -> Unit,
    onEndTemporarySpeed: (Long) -> Unit,
    onSeekPreview: (Long?) -> Unit,
    onSpeedPreview: (Float?) -> Unit,
    onGestureActive: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val current by rememberUpdatedState(state)
    val toggle by rememberUpdatedState(onToggleControls)
    val playPause by rememberUpdatedState(onPlayPause)
    val seek by rememberUpdatedState(onSeekTo)
    val beginSpeed by rememberUpdatedState(onBeginTemporarySpeed)
    val updateSpeed by rememberUpdatedState(onTemporarySpeedChange)
    val endSpeed by rememberUpdatedState(onEndTemporarySpeed)
    val previewSeek by rememberUpdatedState(onSeekPreview)
    val previewSpeed by rememberUpdatedState(onSpeedPreview)
    val gestureActive by rememberUpdatedState(onGestureActive)
    val scope = rememberCoroutineScope()
    val configuration = LocalViewConfiguration.current
    val description = stringResource(R.string.player_video_area)
    var singleTapJob by remember { mutableStateOf<Job?>(null) }
    DisposableEffect(enabled, state.playbackIdentity, state.mediaId) { onDispose { singleTapJob?.cancel() } }
    Box(modifier.semantics {
        contentDescription = description
        if (enabled) onClick { toggle(); true }
    }.pointerInput(enabled, state.playbackIdentity, state.mediaId, state.playWhenReady, state.canSeek, state.canSetSpeed) {
        if (!enabled) return@pointerInput
        var lastTapTime = Long.MIN_VALUE
        var lastTapPosition = Offset.Zero
        awaitEachGesture {
            var speedToken: Long? = null
            try {
                val down = awaitFirstDown(requireUnconsumed = true)
                val origin = down.position
                val originalPosition = current.positionMs
                val duration = current.durationMs?.takeIf { it > 0L }
                var latest: PointerInputChange = down
                val start = withTimeoutOrNull(configuration.longPressTimeoutMillis) {
                    var result: GestureStart? = null
                    while (result == null) {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull { it.id == down.id }
                        if (change == null || change.isConsumed || event.changes.count { it.pressed } > 1) {
                            result = GestureStart.CANCEL
                        } else {
                            latest = change
                            val distance = change.position - origin
                            result = when {
                                !change.pressed -> GestureStart.TAP
                                abs(distance.x) > configuration.touchSlop && abs(distance.x) > abs(distance.y) -> GestureStart.SEEK
                                abs(distance.y) > configuration.touchSlop -> GestureStart.CANCEL
                                else -> null
                            }
                        }
                    }
                    result
                } ?: GestureStart.HOLD
                when (start) {
                    GestureStart.TAP -> {
                        latest.consume()
                        val interval = latest.uptimeMillis - lastTapTime
                        if (lastTapTime != Long.MIN_VALUE && interval in configuration.doubleTapMinTimeMillis..configuration.doubleTapTimeoutMillis &&
                            (latest.position - lastTapPosition).getDistance() < configuration.touchSlop * 6f) {
                            singleTapJob?.cancel()
                            lastTapTime = Long.MIN_VALUE
                            if (current.canPlayPause) playPause()
                        } else {
                            lastTapTime = latest.uptimeMillis
                            lastTapPosition = latest.position
                            singleTapJob?.cancel()
                            singleTapJob = scope.launch { delay(configuration.doubleTapTimeoutMillis); toggle() }
                        }
                    }
                    GestureStart.SEEK, GestureStart.HOLD -> {
                        singleTapJob?.cancel()
                        lastTapTime = Long.MIN_VALUE
                        if (start == GestureStart.SEEK && (!current.canSeek || duration == null)) return@awaitEachGesture
                        if (start == GestureStart.HOLD) {
                            if (!current.playWhenReady || !current.canSetSpeed) return@awaitEachGesture
                            speedToken = beginSpeed() ?: return@awaitEachGesture
                        }
                        gestureActive(true)
                        var target = originalPosition
                        var previousRate: Float? = null
                        var change = latest
                        while (true) {
                            if (change.isConsumed) break
                            change.consume()
                            val fraction = (change.position.x - origin.x) / size.width.coerceAtLeast(1)
                            if (start == GestureStart.SEEK) {
                                target = gestureSeekPosition(originalPosition, checkNotNull(duration), fraction)
                                previewSeek(target)
                            } else {
                                val rate = gesturePlaybackSpeed(fraction)
                                if (rate != previousRate) {
                                    updateSpeed(checkNotNull(speedToken), rate)
                                    previewSpeed(rate)
                                    previousRate = rate
                                }
                            }
                            if (!change.pressed) {
                                if (start == GestureStart.SEEK && current.canSeek) seek(target)
                                break
                            }
                            val event = awaitPointerEvent()
                            if (event.changes.count { it.pressed } > 1) break
                            change = event.changes.firstOrNull { it.id == down.id } ?: break
                        }
                    }
                    GestureStart.CANCEL -> { singleTapJob?.cancel(); lastTapTime = Long.MIN_VALUE }
                }
            } finally {
                speedToken?.let(endSpeed)
                previewSeek(null)
                previewSpeed(null)
                gestureActive(false)
            }
        }
    })
}
