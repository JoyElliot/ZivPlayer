// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.background
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.blur.LayerBackdrop
import top.yukonga.miuix.kmp.blur.blur
import top.yukonga.miuix.kmp.blur.drawBackdrop
import io.github.joyelliot.zivplayer.designsystem.liquid.lens
import io.github.joyelliot.zivplayer.designsystem.liquid.LiquidNavigationMotion

/** Page chrome shared by the library, history and settings. */
@Composable
fun ZivPageHeader(
    title: String,
    leading: (@Composable () -> Unit)? = null,
    actions: @Composable RowScope.() -> Unit = {},
) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 72.dp)
            .padding(start = if (leading == null) 24.dp else 4.dp, end = 12.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        leading?.invoke()
        Text(
            title, Modifier.weight(1f).semantics { heading() },
            color = MiuixTheme.colorScheme.onBackground,
            fontSize = if (leading == null) 28.sp else 20.sp,
            fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
        actions()
    }
}

@Composable
fun ZivSearchEntry(label: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp))
            .background(MiuixTheme.colorScheme.onBackground.copy(alpha = 0.05f))
            .clickable(role = Role.Button, onClick = onClick)
            .semantics { contentDescription = label }
            .heightIn(min = 48.dp).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        ZivPlaybackGlyph(ZivPlaybackIcon.SEARCH, Modifier.size(20.dp), color = MiuixTheme.colorScheme.onSurfaceVariantSummary)
        Text(label, color = MiuixTheme.colorScheme.onSurfaceVariantSummary, fontSize = 14.sp,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun ZivLibraryTile(icon: ZivPlaybackIcon, modifier: Modifier = Modifier, cornerRadius: Dp = 16.dp) {
    val colors = MiuixTheme.colorScheme
    Box(
        modifier.size(48.dp).clip(RoundedCornerShape(cornerRadius))
            .background(colors.primary.copy(alpha = 0.10f)),
        contentAlignment = Alignment.Center,
    ) {
        ZivPlaybackGlyph(icon, Modifier.size(25.dp), color = colors.primary)
    }
}

data class ZivNavigationItem(val label: String, val icon: ZivPlaybackIcon, val testTag: String)

/** Uses the reference navigation's press, drag and refracted icon layers. */
@Composable
fun ZivNavigationBar(items: List<ZivNavigationItem>, selectedIndex: Int, onSelect: (Int) -> Unit,
    nowPlaying: (@Composable () -> Unit)? = null, nowPlayingProgress: Float? = null) {
    require(items.isNotEmpty() && selectedIndex in items.indices)
    val backdrop = LocalHomeBackdrop.current
    val motion = remember { LiquidNavigationMotion() }
    val bottomClearance = if (WindowInsets.navigationBars.getBottom(LocalDensity.current) > 0) 10.dp else 26.dp
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

@Composable
fun ZivMiniPlayerSurface(onClick: () -> Unit, content: @Composable () -> Unit) {
    val shape = RoundedCornerShape(12.dp)
    Box(Modifier.fillMaxWidth().clip(shape).clickable(role = Role.Button, onClick = onClick)) {
        content()
    }
}

/** A full touch target with feedback inset from the compact playback row's edges. */
@Composable
fun ZivMiniPlayerButton(icon: ZivPlaybackIcon, description: String, onClick: () -> Unit, enabled: Boolean) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val focused by interaction.collectIsFocusedAsState()
    val alpha by animateFloatAsState(if (enabled && (pressed || focused)) 0.12f else 0f,
        tween(120), label = "miniPlaybackPress")
    val color = MiuixTheme.colorScheme.onBackground
    Box(Modifier.size(48.dp)
        .clickable(interactionSource = interaction, indication = null, enabled = enabled,
            role = Role.Button, onClick = onClick)
        .semantics { contentDescription = description }, contentAlignment = Alignment.Center) {
        Box(Modifier.size(36.dp).background(color.copy(alpha = alpha), CircleShape)
            .testTag("mini_playback_feedback"), contentAlignment = Alignment.Center) {
            ZivPlaybackGlyph(icon, Modifier.size(24.dp), enabled, color)
        }
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
