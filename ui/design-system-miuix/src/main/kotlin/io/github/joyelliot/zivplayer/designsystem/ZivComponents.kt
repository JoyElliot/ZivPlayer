// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.runtime.getValue
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import top.yukonga.miuix.kmp.theme.MiuixTheme
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.CircularProgressIndicator
import top.yukonga.miuix.kmp.basic.LinearProgressIndicator
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Slider
import top.yukonga.miuix.kmp.basic.SmallTopAppBar
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextField
import top.yukonga.miuix.kmp.basic.Switch

@Composable
fun ZivScreen(
    title: String,
    modifier: Modifier = Modifier,
    bottomPadding: Dp = 0.dp,
    leading: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    ZivLibrarySurface(modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
        ZivPageHeader(title, leading = leading)
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp, vertical = 16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            content()
            Spacer(Modifier.height(bottomPadding))
        }
    }
}

@Composable
fun ZivPrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val colors = MiuixTheme.colorScheme
    Box(modifier.heightIn(min = 48.dp).clip(RoundedCornerShape(16.dp))
        .background(colors.primary.copy(alpha = if (enabled) 1f else 0.3f))
        .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
        .padding(horizontal = 20.dp, vertical = 12.dp), contentAlignment = Alignment.Center) {
        Text(text, color = colors.onPrimary, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
    }
}

@Composable
fun ZivCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(20.dp)
    Column(modifier.fillMaxWidth().clip(shape).background(MiuixTheme.colorScheme.surfaceContainer)
        .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier),
        content = content)
}

@Composable
fun ZivSlider(
    value: Float,
    onValueChange: (Float) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    valueRange: ClosedFloatingPointRange<Float> = 0f..1f,
    onValueChangeFinished: (() -> Unit)? = null,
    stateDescription: String? = null,
) {
    val describedModifier = modifier.semantics {
        stateDescription?.let { this.stateDescription = it }
        if (!enabled) {
            disabled()
        }
    }
    val span = (valueRange.endInclusive - valueRange.start).coerceAtLeast(0.0001f)
    ZivPlaybackSlider((value.coerceIn(valueRange) - valueRange.start) / span,
        { onValueChange(valueRange.start + it * span) }, { onValueChangeFinished?.invoke() },
        describedModifier.fillMaxWidth(), enabled, description = stateDescription.orEmpty(),
        trackColor = MiuixTheme.colorScheme.onBackground)
}

@Composable
fun ZivLinearProgress(
    progress: Float,
    modifier: Modifier = Modifier,
) {
    LinearProgressIndicator(
        modifier = modifier.fillMaxWidth(),
        progress = progress.coerceIn(0f, 1f),
    )
}

@Composable
fun ZivLoadingIndicator(modifier: Modifier = Modifier) {
    CircularProgressIndicator(modifier = modifier)
}

@Composable
fun ZivText(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text = text,
        modifier = modifier,
    )
}

@Composable
fun ZivStatusText(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text = text,
        modifier = modifier.semantics { liveRegion = LiveRegionMode.Polite },
    )
}

@Composable
fun ZivToggleRow(title: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit,
    detail: String? = null, enabled: Boolean = true) {
    val colors = MiuixTheme.colorScheme
    val progress by animateFloatAsState(if (checked) 1f else 0f,
        spring(dampingRatio = 0.85f, stiffness = 600f), label = "toggle")
    Row(Modifier.fillMaxWidth().heightIn(min = 64.dp).clip(RoundedCornerShape(16.dp))
        .toggleable(checked, enabled = enabled, role = Role.Switch, onValueChange = onCheckedChange)
        .padding(horizontal = 4.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, color = colors.onBackground.copy(alpha = if (enabled) 1f else 0.4f), fontSize = 15.sp)
            detail?.let { Text(it, color = colors.onSurfaceVariantSummary, fontSize = 12.sp) }
        }
        Spacer(Modifier.width(12.dp))
        Canvas(Modifier.size(52.dp, 48.dp)) {
            val t = progress.coerceIn(0f, 1f)
            val track = lerp(colors.onBackground.copy(alpha = 0.15f), colors.primary, t)
            drawRoundRect(track.copy(alpha = track.alpha * if (enabled) 1f else 0.4f),
                Offset(2.dp.toPx(), 9.dp.toPx()), Size(48.dp.toPx(), 30.dp.toPx()), CornerRadius(15.dp.toPx()))
            drawCircle(Color.White.copy(alpha = if (enabled) 1f else 0.7f), 11.dp.toPx(),
                Offset((17 + 18 * t).dp.toPx(), 24.dp.toPx()))
        }
    }
}

@Composable
fun ZivTextField(value: String, onValueChange: (String) -> Unit, label: String,
    modifier: Modifier = Modifier) {
    TextField(value = value, onValueChange = onValueChange, label = label,
        singleLine = true, modifier = modifier.fillMaxWidth())
}

/** A non-scrolling scaffold for virtualized lists owned by the feature. */
@Composable
fun ZivListScreen(title: String, modifier: Modifier = Modifier,
    content: @Composable (Modifier) -> Unit) {
    Scaffold(modifier = modifier, topBar = { SmallTopAppBar(title = title) }) { padding ->
        content(Modifier.fillMaxSize().padding(padding))
    }
}
