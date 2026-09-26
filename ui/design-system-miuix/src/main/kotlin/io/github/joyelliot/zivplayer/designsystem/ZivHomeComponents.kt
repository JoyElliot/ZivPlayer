// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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
fun ZivNavigationBar(items: List<ZivNavigationItem>, selectedIndex: Int, onSelect: (Int) -> Unit) {
    require(items.isNotEmpty() && selectedIndex in items.indices)
    val backdrop = LocalHomeBackdrop.current
    io.github.joyelliot.zivplayer.designsystem.liquid.LiquidGlassNavigationBar(
        items, selectedIndex, onSelect, backdrop, isBlurActive = backdrop != null,
    )
}

@Composable
fun ZivMiniPlayerSurface(modifier: Modifier = Modifier, onClick: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    val colors = MiuixTheme.colorScheme
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp))
            .homeGlass(24.dp, colors.surfaceContainer.copy(alpha = 0.65f), blurRadius = 8.dp)
            .border(1.dp, colors.primary.copy(alpha = 0.10f), RoundedCornerShape(24.dp))
            .clickable(role = Role.Button, onClick = onClick),
        content = content,
    )
}

@Composable
fun ZivMiniPlayerProgress(progress: Float) {
    Box(Modifier.fillMaxWidth().height(2.dp).background(MiuixTheme.colorScheme.primary.copy(alpha = 0.08f))) {
        Box(Modifier.fillMaxWidth(progress.coerceIn(0f, 1f)).fillMaxHeight().background(MiuixTheme.colorScheme.primary))
    }
}
