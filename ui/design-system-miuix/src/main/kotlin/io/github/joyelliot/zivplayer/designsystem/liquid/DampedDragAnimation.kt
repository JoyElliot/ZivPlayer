// Copyright 2026, compose-miuix-ui contributors
// SPDX-License-Identifier: Apache-2.0
// Modified for ZivPlayer in 2026: adapted from FangFeishu commit 8676095d.
// See THIRD_PARTY_NOTICES.md and licenses/Apache-2.0.txt.

package io.github.joyelliot.zivplayer.designsystem.liquid

// Adapted from Kyant0/AndroidLiquidGlass — https://github.com/Kyant0/AndroidLiquidGlass (Apache 2.0).

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.MutatorMutex
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameMillis
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlin.math.abs

internal class DampedDragAnimation(
    private val animationScope: CoroutineScope,
    val initialValue: Float,
    val valueRange: ClosedRange<Float>,
    val visibilityThreshold: Float,
    val initialScale: Float,
    val pressedScale: Float,
) {

    private val valueAnimationSpec = spring(1f, 1000f, visibilityThreshold)
    private val velocityAnimationSpec = spring(0.5f, 300f, visibilityThreshold * 10f)
    private val pressProgressAnimationSpec = spring(1f, 1000f, 0.001f)
    private val scaleXAnimationSpec = spring(0.6f, 250f, 0.001f)
    private val scaleYAnimationSpec = spring(0.7f, 250f, 0.001f)

    private val valueAnimation = Animatable(initialValue, visibilityThreshold)
    private val velocityAnimation = Animatable(0f, 5f)
    private val pressProgressAnimation = Animatable(0f, 0.001f)
    private val scaleXAnimation = Animatable(initialScale, 0.001f)
    private val scaleYAnimation = Animatable(initialScale, 0.001f)

    private val mutatorMutex = MutatorMutex()

    private var pressJob: Job? = null
    private var releaseJob: Job? = null

    val value: Float get() = valueAnimation.value
    val targetValue: Float get() = valueAnimation.targetValue
    val pressProgress: Float get() = pressProgressAnimation.value
    val scaleX: Float get() = scaleXAnimation.value
    val scaleY: Float get() = scaleYAnimation.value
    val velocity: Float get() = velocityAnimation.value

    fun press() {
        releaseJob?.cancel()
        pressJob?.cancel()
        pressJob = animationScope.launch {
            launch { pressProgressAnimation.animateTo(1f, pressProgressAnimationSpec) }
            launch { scaleXAnimation.animateTo(pressedScale, scaleXAnimationSpec) }
            launch { scaleYAnimation.animateTo(pressedScale, scaleYAnimationSpec) }
        }
    }

    fun release() {
        releaseJob?.cancel()
        releaseJob = animationScope.launch {
            withFrameMillis { }
            if (value != targetValue) {
                val threshold = (valueRange.endInclusive - valueRange.start) * 0.025f
                snapshotFlow { valueAnimation.value }.first { abs(it - valueAnimation.targetValue) < threshold }
            }
            launch { pressProgressAnimation.animateTo(0f, pressProgressAnimationSpec) }
            launch { scaleXAnimation.animateTo(initialScale, scaleXAnimationSpec) }
            launch { scaleYAnimation.animateTo(initialScale, scaleYAnimationSpec) }
        }
    }

    fun updateValue(value: Float) {
        val targetValue = value.coerceIn(valueRange)
        animationScope.launch {
            valueAnimation.animateTo(targetValue, valueAnimationSpec) { updateVelocity() }
            velocityAnimation.animateTo(0f, velocityAnimationSpec)
        }
    }

    /**
     * 仅切换选中进度并带缩放动画（单击 Tab）；不经过拖动手势。
     * 时长随 |Δvalue| 略增，避免底栏玻璃跨多 Tab 时与页面滚动一样显得过快。
     */
    fun animateToValue(value: Float) {
        animationScope.launch {
            mutatorMutex.mutate {
                press()
                val targetValue = value.coerceIn(valueRange)
                val distance = abs(targetValue - valueAnimation.value).coerceAtLeast(0.25f)
                // 保留参考项目的距离档位：1 格 300 · 2 格 400 · 3+ 格 500。
                val durationMs = when {
                    distance < 1.5f -> 300
                    distance < 2.5f -> 400
                    else -> 500
                }
                launch {
                    valueAnimation.animateTo(
                        targetValue,
                        tween(durationMs, easing = FastOutSlowInEasing),
                    )
                }
                if (velocity != 0f) {
                    launch { velocityAnimation.animateTo(0f, velocityAnimationSpec) }
                }
                release()
            }
        }
    }

    private fun updateVelocity() {
        val span = (valueRange.endInclusive - valueRange.start).coerceAtLeast(1e-6f)
        // Use the spring's frame-time velocity; decay after settling so a stationary
        // held capsule cannot retain a stale stretch from the last pointer movement.
        val targetVelocity = valueAnimation.velocity / span
        animationScope.launch(start = CoroutineStart.UNDISPATCHED) {
            velocityAnimation.snapTo(targetVelocity)
        }
    }
}
