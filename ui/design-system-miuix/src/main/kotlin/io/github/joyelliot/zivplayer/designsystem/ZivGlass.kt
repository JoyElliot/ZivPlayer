// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import top.yukonga.miuix.kmp.blur.BackdropEffectScope
import top.yukonga.miuix.kmp.blur.LayerBackdrop
import top.yukonga.miuix.kmp.blur.blur
import top.yukonga.miuix.kmp.blur.drawBackdrop
import top.yukonga.miuix.kmp.blur.highlight.Highlight
import top.yukonga.miuix.kmp.blur.layerBackdrop
import top.yukonga.miuix.kmp.blur.rememberLayerBackdrop
import top.yukonga.miuix.kmp.blur.runtimeShaderEffect
import top.yukonga.miuix.kmp.theme.MiuixTheme
import androidx.compose.foundation.shape.RoundedCornerShape

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

/** A live blurred page sample with a bounded, rounded lens and a light-catching rim. */
@Composable
internal fun Modifier.homeGlass(radius: Dp, tint: Color, blurRadius: Dp = 4.dp): Modifier {
    val backdrop = LocalHomeBackdrop.current
    val shape = RoundedCornerShape(radius)
    val dark = MiuixTheme.colorScheme.background.luminance() < 0.5f
    return if (backdrop == null) background(tint.copy(alpha = 1f), shape) else drawBackdrop(
        backdrop = backdrop,
        shape = { shape },
        effects = {
            // Reserve the lens sampling extent before the blur builds its padded uniforms.
            padding = maxOf(padding, 16.dp.toPx())
            blur(blurRadius.toPx())
            roundedLens(radius.toPx(), 14.dp.toPx())
        },
        highlight = { if (dark) Highlight.GlassStrokeMiddleDark else Highlight.GlassStrokeMiddleLight },
        onDrawSurface = { drawRect(tint) },
    )
}

private fun BackdropEffectScope.roundedLens(radius: Float, amount: Float) {
    val scale = downscaleFactor.coerceAtLeast(1).toFloat()
    val width = size.width / scale
    val height = size.height / scale
    val inset = padding / scale
    val corner = minOf(radius, size.minDimension / 2f) / scale
    val depth = amount / scale
    runtimeShaderEffect("ZivRoundedLens", RoundedLensShader, "page") {
        setFloatUniform("extent", width, height)
        setFloatUniform("inset", inset)
        setFloatUniform("radius", corner)
        setFloatUniform("depth", depth)
    }
}

// Only the rim displaces the sample; the interior remains still while text stays unfiltered.
// All lengths use the backdrop's downscaled pixel space. Epsilon guards the corner normal.
private const val RoundedLensShader = """
uniform shader page;
uniform float2 extent;
uniform float inset;
uniform float radius;
uniform float depth;
half4 main(float2 p) {
    float2 centered = p - float2(inset) - extent * 0.5;
    float2 q = abs(centered) - extent * 0.5 + float2(radius);
    float2 outside = max(q, float2(0.0));
    float distance = length(outside) + min(max(q.x, q.y), 0.0) - radius;
    float rim = clamp(1.0 + distance / max(radius, 1.0), 0.0, 1.0);
    float2 normal;
    if (max(q.x, q.y) > 0.0) {
        normal = sign(centered) * outside / max(length(outside), 0.001);
    } else {
        normal = q.x > q.y ? float2(sign(centered.x), 0.0) : float2(0.0, sign(centered.y));
    }
    float bend = (1.0 - sqrt(max(1.0 - rim * rim, 0.0))) * depth;
    return page.eval(p - normal * bend);
}
"""
