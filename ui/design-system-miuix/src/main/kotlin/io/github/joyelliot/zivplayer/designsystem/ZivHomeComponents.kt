// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
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
fun ZivLibraryTile(icon: ZivPlaybackIcon, modifier: Modifier = Modifier) {
    val colors = MiuixTheme.colorScheme
    Box(
        modifier.size(48.dp).clip(RoundedCornerShape(16.dp))
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
    io.github.joyelliot.zivplayer.designsystem.liquid.LiquidGlassNavigationBar(
        items, selectedIndex, onSelect, backdrop, isBlurActive = backdrop != null,
        nowPlaying = nowPlaying,
        nowPlayingProgress = nowPlayingProgress,
    )
}

@Composable
fun ZivMiniPlayerSurface(onClick: () -> Unit, content: @Composable () -> Unit) {
    val shape = RoundedCornerShape(24.dp)
    Box(Modifier.fillMaxWidth().clip(shape).clickable(role = Role.Button, onClick = onClick)) {
        content()
    }
}

/** Progress follows the upper rim and takes no space away from the card content. */
@Composable
internal fun ZivMiniPlayerProgress(progress: Float, modifier: Modifier) {
    val color = MiuixTheme.colorScheme.primary
    Canvas(modifier) {
        val stroke = 2.dp.toPx()
        val inset = stroke / 2f
        val side = 12.dp.toPx()
        val radius = minOf(24.dp.toPx(), size.height / 2f, size.width / 2f)
        val edge = Path().apply {
            arcTo(Rect(side + inset, inset, side + radius * 2 - inset, radius * 2 - inset), 180f, 90f, true)
            lineTo(size.width - side - radius, inset)
            arcTo(Rect(size.width - side - radius * 2 + inset, inset, size.width - side - inset, radius * 2 - inset), 270f, 90f, false)
        }
        val measure = PathMeasure().apply { setPath(edge, false) }
        val segment = Path()
        measure.getSegment(0f, measure.length * progress.coerceIn(0f, 1f), segment)
        drawPath(segment, color, style = Stroke(stroke, cap = StrokeCap.Round))
    }
}
