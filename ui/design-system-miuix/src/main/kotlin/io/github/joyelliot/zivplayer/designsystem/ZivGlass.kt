// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import top.yukonga.miuix.kmp.blur.LayerBackdrop
import top.yukonga.miuix.kmp.blur.layerBackdrop
import top.yukonga.miuix.kmp.blur.rememberLayerBackdrop
import top.yukonga.miuix.kmp.theme.MiuixTheme

internal val LocalHomeBackdrop = staticCompositionLocalOf<LayerBackdrop?> { null }

/** Capture only page content, never the glass that samples it. */
@Composable
fun ZivHomeScaffold(bottomBar: @Composable ColumnScope.() -> Unit, content: @Composable (Dp) -> Unit) {
    val background = MiuixTheme.colorScheme.background
    val backdrop = rememberLayerBackdrop { drawRect(background); drawContent() }
    var bottomHeight by remember { mutableIntStateOf(0) }
    val bottomPadding = with(LocalDensity.current) { bottomHeight.toDp() }
    Box(Modifier.fillMaxSize().background(background)
        .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom))
        .imePadding()) {
        Box(Modifier.fillMaxSize().layerBackdrop(backdrop)) { content(bottomPadding) }
        CompositionLocalProvider(LocalHomeBackdrop provides backdrop) {
            Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().onSizeChanged { bottomHeight = it.height }, content = bottomBar)
        }
    }
}
