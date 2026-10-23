// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.liquid.LiquidNavigationMotion
import io.github.joyelliot.zivplayer.designsystem.liquid.lens
import top.yukonga.miuix.kmp.blur.LayerBackdrop
import top.yukonga.miuix.kmp.blur.blur
import top.yukonga.miuix.kmp.blur.drawBackdrop
import top.yukonga.miuix.kmp.theme.MiuixTheme

/** Uses the reference navigation's press, drag and refracted icon layers. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ZivGlassNavigationBar(items: List<ZivNavigationItem>, selectedIndex: Int, onSelect: (Int) -> Unit,
    nowPlaying: (@Composable () -> Unit)? = null, nowPlayingProgress: Float? = null) {
    require(items.isNotEmpty() && selectedIndex in items.indices)
    val backdrop = LocalHomeBackdrop.current
    val motion = remember { LiquidNavigationMotion() }
    val bottomClearance = if (ZivInsets.navigationBars.getBottom(LocalDensity.current) > 0) 10.dp else 26.dp
    Box(Modifier.fillMaxWidth().testTag("app_playback_dock")) {
        if (nowPlaying != null) {
            // An open frame surrounds only the upper half of the original navigation.
            // Share the navigation width and transform so both sides meet its center line without a gap.
            Box(Modifier.matchParentSize(), contentAlignment = Alignment.Center) {
                PlaybackCardBackground(
                    backdrop, nowPlayingProgress,
                    Modifier.padding(start = 32.dp, end = 32.dp, top = 8.dp, bottom = bottomClearance + 32.dp)
                        .widthIn(max = 480.dp).fillMaxWidth().fillMaxHeight()
                        .graphicsLayer {
                            val frame = motion.source()
                            transformOrigin = TransformOrigin(0.5f, 1f)
                            translationX = frame.offsetPx
                            scaleX = frame.panelScale
                            scaleY = frame.panelScale
                        },
                )
            }
        }
        // Cancel only the navigation's outer 8 dp top margin, never overlap its hit targets.
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy((-8).dp)) {
            nowPlaying?.let { content ->
                Box(Modifier.fillMaxWidth().padding(top = 8.dp), contentAlignment = Alignment.Center) {
                    Box(Modifier.padding(horizontal = 40.dp).widthIn(max = 464.dp).fillMaxWidth()
                        .graphicsLayer {
                            val frame = motion.source()
                            // Share the navigation's center as the scale pivot, including the 32 dp below this row.
                            transformOrigin = TransformOrigin(0.5f, 1f + 32.dp.toPx() / size.height.coerceAtLeast(1f))
                            translationX = frame.offsetPx
                            scaleX = frame.panelScale
                            scaleY = frame.panelScale
                        }) { content() }
                }
            }
            io.github.joyelliot.zivplayer.designsystem.liquid.LiquidGlassNavigationBar(
                items, selectedIndex, onSelect, backdrop, isBlurActive = backdrop != null, motion = motion,
            )
        }
    }
}

@Composable
private fun PlaybackCardBackground(backdrop: LayerBackdrop?, progress: Float?, modifier: Modifier) {
    val colors = MiuixTheme.colorScheme
    val dark = colors.background.luminance() < 0.5f
    val shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
    Box(modifier.testTag("app_playback_card")
        .then(if (backdrop != null) Modifier.drawBackdrop(
            backdrop, shape = { shape },
            effects = {
                padding = maxOf(padding, 20.dp.toPx())
                blur(4.dp.toPx())
                lens(20.dp.toPx(), 20.dp.toPx())
            },
            onDrawSurface = { drawRect(colors.surfaceContainer.copy(alpha = 0.20f)) },
        ) else Modifier.background(colors.surfaceContainer, shape))) {
        PlaybackCardRim(progress, dark, Modifier.matchParentSize())
    }
}

/** The open rim stops at the navigation center; playback progress occupies its upper edge. */
@Composable
private fun PlaybackCardRim(progress: Float?, dark: Boolean, modifier: Modifier) {
    val color = MiuixTheme.colorScheme.primary
    Canvas(modifier) {
        val stroke = 2.dp.toPx()
        val inset = stroke / 2f
        val radius = minOf(20.dp.toPx(), size.height / 2f, size.width / 2f)
        val upperEdge = Path().apply {
            arcTo(Rect(inset, inset, radius * 2 - inset, radius * 2 - inset), 180f, 90f, true)
            lineTo(size.width - radius, inset)
            arcTo(Rect(size.width - radius * 2 + inset, inset, size.width - inset, radius * 2 - inset), 270f, 90f, false)
        }
        val openFrame = Path().apply {
            moveTo(inset, size.height)
            lineTo(inset, radius)
            addPath(upperEdge)
            moveTo(size.width - inset, radius)
            lineTo(size.width - inset, size.height)
        }
        drawPath(openFrame,
            Brush.verticalGradient(listOf(Color.White.copy(alpha = if (dark) 0.30f else 0.90f),
                Color.White.copy(alpha = if (dark) 0.08f else 0.30f))),
            style = Stroke(1.dp.toPx(), cap = StrokeCap.Round))
        progress?.let {
            val measure = PathMeasure().apply { setPath(upperEdge, false) }
            val segment = Path()
            measure.getSegment(0f, measure.length * it.coerceIn(0f, 1f), segment)
            drawPath(segment, color, style = Stroke(stroke, cap = StrokeCap.Round))
        }
    }
}
