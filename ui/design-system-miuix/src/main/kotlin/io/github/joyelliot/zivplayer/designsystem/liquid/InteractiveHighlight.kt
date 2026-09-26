// Copyright 2026, compose-miuix-ui contributors
// SPDX-License-Identifier: Apache-2.0
// Modified for ZivPlayer in 2026: adapted from FangFeishu commit 8676095d.
// See THIRD_PARTY_NOTICES.md and licenses/Apache-2.0.txt.

package io.github.joyelliot.zivplayer.designsystem.liquid

// Adapted from Kyant0/AndroidLiquidGlass — https://github.com/Kyant0/AndroidLiquidGlass (Apache 2.0).

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

internal class InteractiveHighlight(
    private val animationScope: CoroutineScope,
    private val position: (size: Size, offset: Offset) -> Offset = { _, offset -> offset },
) {

    private val pressProgressAnimationSpec = spring(0.5f, 300f, 0.001f)

    private val pressProgressAnimation = Animatable(0f, 0.001f)
    private var pointerPosition by mutableStateOf(Offset.Zero)

    val modifier: Modifier = Modifier.drawWithContent {
        val progress = pressProgressAnimation.value
        if (progress > 0f) {
            drawRect(
                color = Color.White.copy(alpha = 0.04f * progress),
                blendMode = BlendMode.Plus,
            )
            val pos = position(size, pointerPosition)
            val radius = (size.minDimension * 1.2f).coerceAtLeast(1f)
            val center = Offset(
                x = pos.x.coerceIn(0f, size.width),
                y = pos.y.coerceIn(0f, size.height),
            )
            val spotColor = Color.White.copy(alpha = 0.04f * progress)
            drawRect(
                brush = Brush.radialGradient(
                    colorStops = arrayOf(
                        0.0f to spotColor,
                        0.5f to spotColor,
                        1.0f to Color.White.copy(alpha = 0f),
                    ),
                    center = center,
                    radius = radius,
                ),
                blendMode = BlendMode.Plus,
            )
        }
        drawContent()
    }

    fun press(position: Offset) {
        pointerPosition = position
        animationScope.launch { pressProgressAnimation.animateTo(1f, pressProgressAnimationSpec) }
    }

    fun move(position: Offset) { pointerPosition = position }

    fun release() {
        animationScope.launch {
            pressProgressAnimation.animateTo(0f, pressProgressAnimationSpec)
        }
    }
}
