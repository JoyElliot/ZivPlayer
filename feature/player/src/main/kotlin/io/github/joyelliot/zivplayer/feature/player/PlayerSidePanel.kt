// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties
import io.github.joyelliot.zivplayer.designsystem.*

@Composable
internal fun PlayerSidePanel(title: String, closeDescription: String, onDismiss: () -> Unit,
    onBack: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    // Use the full window for both measurement and placement. Clipping to Android's
    // cutout-safe visible frame otherwise shifts a full-width overlay past the right edge.
    Popup(popupPositionProvider = WindowOrigin, onDismissRequest = onDismiss,
        properties = PopupProperties(focusable = true, clippingEnabled = false, usePlatformDefaultWidth = false)) {
        ZivTheme(ZivAppearance.DARK) {
            BoxWithConstraints(Modifier.fillMaxSize()) {
                Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = 0.16f))
                    .pointerInput(onDismiss) { detectTapGestures { onDismiss() } })
                val width = (maxWidth * if (maxWidth > 600.dp) 0.46f else 0.94f).coerceAtMost(420.dp)
                Column(Modifier.align(Alignment.CenterEnd).width(width).fillMaxHeight()
                    .background(Brush.horizontalGradient(listOf(Color(0xEE141820), Color(0xF5141820))))
                    .pointerInput(Unit) { detectTapGestures { } }
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Bottom + WindowInsetsSides.End))
                    .padding(horizontal = 16.dp, vertical = 8.dp)) {
                    Row(Modifier.fillMaxWidth().height(48.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (onBack != null) ZivPlaybackIconButton(ZivPlaybackIcon.BACK, stringResource(R.string.player_back), onBack)
                        ZivPlaybackText(title, Modifier.weight(1f).padding(start = if (onBack == null) 8.dp else 0.dp), title = true, compact = true)
                        ZivPlaybackIconButton(ZivPlaybackIcon.CLOSE, closeDescription, onDismiss)
                    }
                    content()
                }
            }
        }
    }
}

private object WindowOrigin : PopupPositionProvider {
    override fun calculatePosition(anchorBounds: IntRect, windowSize: IntSize, layoutDirection: LayoutDirection, popupContentSize: IntSize) = IntOffset.Zero
}
