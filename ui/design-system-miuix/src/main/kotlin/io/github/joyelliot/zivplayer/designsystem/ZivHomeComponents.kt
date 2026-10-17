// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.animation.core.spring
import androidx.compose.foundation.border
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.lerp
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

/** Flat navigation keeps playback and destinations together without sampling the video layer. */
@Composable
fun ZivNavigationBar(items: List<ZivNavigationItem>, selectedIndex: Int, onSelect: (Int) -> Unit,
    nowPlaying: (@Composable () -> Unit)? = null, nowPlayingProgress: Float? = null) {
    require(items.isNotEmpty() && selectedIndex in items.indices)
    val colors = MiuixTheme.colorScheme
    Box(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)
        .testTag("app_playback_dock"), contentAlignment = Alignment.Center) {
        Column(Modifier.widthIn(max = 520.dp).fillMaxWidth().clip(RoundedCornerShape(24.dp))
            .background(colors.surfaceContainer)
            .border(1.dp, colors.onBackground.copy(alpha = 0.06f), RoundedCornerShape(24.dp))) {
            nowPlaying?.let { content ->
                Box(Modifier.fillMaxWidth().padding(start = 12.dp, end = 8.dp, top = 8.dp, bottom = 4.dp)
                    .testTag("app_playback_card")) { content() }
                Canvas(Modifier.fillMaxWidth().padding(horizontal = 16.dp).height(2.dp)) {
                    drawLine(colors.onBackground.copy(alpha = 0.06f), Offset.Zero,
                        Offset(size.width, 0f), size.height, StrokeCap.Round)
                    nowPlayingProgress?.let { progress ->
                        drawLine(colors.primary, Offset.Zero, Offset(size.width * progress.coerceIn(0f, 1f), 0f),
                            size.height, StrokeCap.Round)
                    }
                }
            }
            Row(Modifier.fillMaxWidth().selectableGroup().padding(horizontal = 6.dp, vertical = 6.dp)) {
                items.forEachIndexed { index, item ->
                    val selected = index == selectedIndex
                    val emphasis by animateFloatAsState(if (selected) 1f else 0f,
                        spring(dampingRatio = 0.85f, stiffness = 500f), label = "navigation emphasis")
                    Column(Modifier.weight(1f).heightIn(min = 54.dp).clip(RoundedCornerShape(18.dp))
                        .background(colors.primary.copy(alpha = 0.10f * emphasis))
                        .selectable(selected, role = Role.Tab, onClick = { onSelect(index) })
                        .testTag(item.testTag).padding(vertical = 5.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        ZivPlaybackGlyph(item.icon, Modifier.size(23.dp).graphicsLayer {
                            scaleX = 0.94f + emphasis * 0.06f; scaleY = scaleX
                        }, color = lerp(colors.onSurfaceVariantSummary, colors.primary, emphasis.coerceIn(0f, 1f)))
                        Text(item.label, color = if (selected) colors.primary else colors.onSurfaceVariantSummary,
                            fontSize = 11.sp, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
}

@Composable
fun ZivMiniPlayerSurface(onClick: () -> Unit, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val shape = RoundedCornerShape(12.dp)
    Box(modifier.fillMaxWidth().clip(shape).clickable(role = Role.Button, onClick = onClick)) {
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
