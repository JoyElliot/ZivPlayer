// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*

/** In-window panel: layout, pointer coordinates and insets inherit the player's rotation. */
@Composable
internal fun PlayerSidePanel(title: String, closeDescription: String, onDismiss: () -> Unit,
    onBack: (() -> Unit)? = null, insets: WindowInsets = WindowInsets.safeDrawing,
    content: @Composable ColumnScope.() -> Unit) {
    BackHandler { (onBack ?: onDismiss)() }
    var entered by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { entered = true }
    val progress by animateFloatAsState(if (entered) 1f else 0f,
        spring(dampingRatio = 1f, stiffness = 500f), label = "player panel")
    ZivTheme(ZivAppearance.DARK, applySystemBars = false) {
        BoxWithConstraints(Modifier.fillMaxSize().semantics { paneTitle = title }) {
            Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = 0.25f * progress))
                .pointerInput(onDismiss) { detectTapGestures { onDismiss() } })
            val width = (maxWidth * if (maxWidth > 600.dp) 0.46f else 0.94f).coerceAtMost(420.dp)
            Column(Modifier.align(Alignment.CenterEnd).width(width).fillMaxHeight().graphicsLayer {
                translationX = (1 - progress) * 48.dp.toPx(); alpha = progress
            }.background(Color(0xFF161B24), RoundedCornerShape(topStart = 24.dp, bottomStart = 24.dp))
                .pointerInput(Unit) { detectTapGestures { } }
                .windowInsetsPadding(insets.only(WindowInsetsSides.Top + WindowInsetsSides.Bottom + WindowInsetsSides.End))
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
