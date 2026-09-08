// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.theme.MiuixTheme

@Composable
fun ZivLibrarySurface(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.background(MiuixTheme.colorScheme.background), content = content)
}

@Composable
fun ZivLibraryText(text: String, modifier: Modifier = Modifier, secondary: Boolean = false,
    title: Boolean = false, maxLines: Int = 2) {
    Text(text, modifier, color = MiuixTheme.colorScheme.onBackground.copy(alpha = if (secondary) 0.55f else 1f),
        fontSize = if (title) 26.sp else if (secondary) 13.sp else 16.sp,
        fontWeight = if (title) FontWeight.Bold else FontWeight.Normal,
        maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

@Composable
fun ZivLibraryIconButton(icon: ZivPlaybackIcon, description: String, onClick: () -> Unit,
    modifier: Modifier = Modifier, enabled: Boolean = true) {
    ZivPlaybackIconButton(icon, description, onClick, modifier, enabled, color = MiuixTheme.colorScheme.onBackground)
}

@Composable
fun ZivLibraryChip(label: String, selected: Boolean = false, onClick: () -> Unit) {
    Box(Modifier.clip(RoundedCornerShape(24.dp))
        .background(MiuixTheme.colorScheme.onBackground.copy(alpha = if (selected) 0.13f else 0.05f))
        .semantics { this.selected = selected }
        .clickable(role = Role.Tab, onClick = onClick).heightIn(min = 44.dp).padding(horizontal = 20.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center) { ZivLibraryText(label, maxLines = 1) }
}

/** The row and its trailing action have separate touch targets and accessibility actions. */
@Composable
fun ZivLibraryRow(title: String, detail: String?, icon: ZivPlaybackIcon, onClick: () -> Unit,
    modifier: Modifier = Modifier, trailing: @Composable () -> Unit = {}) {
    Row(modifier.fillMaxWidth().heightIn(min = 88.dp).clickable(role = Role.Button, onClick = onClick)
        .padding(start = 20.dp, end = 8.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(56.dp).clip(RoundedCornerShape(14.dp))
            .background(MiuixTheme.colorScheme.onBackground.copy(alpha = 0.06f)), contentAlignment = Alignment.Center) {
            ZivPlaybackGlyph(icon, Modifier.size(32.dp), color = MiuixTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        }
        Column(Modifier.weight(1f).padding(start = 16.dp, end = 8.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            ZivLibraryText(title)
            detail?.takeIf { it.isNotBlank() }?.let { ZivLibraryText(it, secondary = true) }
        }
        trailing()
    }
}

@Composable
fun ZivLibraryDialog(title: String, onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        ZivLibrarySurface(Modifier.fillMaxWidth().heightIn(max = 560.dp).clip(RoundedCornerShape(24.dp)).padding(0.dp)) {
            Row(Modifier.padding(start = 20.dp, top = 8.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                ZivLibraryText(title, Modifier.weight(1f))
                ZivLibraryIconButton(ZivPlaybackIcon.CLOSE, title, onDismiss)
            }
            Column(Modifier.verticalScroll(rememberScrollState()).padding(bottom = 12.dp), content = content)
        }
    }
}

@Composable
fun ZivLibraryMenuItem(label: String, onClick: () -> Unit, selected: Boolean = false) {
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).clickable(role = Role.Button, onClick = onClick)
        .padding(horizontal = 24.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        ZivLibraryText(label, Modifier.weight(1f))
        if (selected) ZivPlaybackGlyph(ZivPlaybackIcon.CHECK, color = MiuixTheme.colorScheme.onBackground)
    }
}
