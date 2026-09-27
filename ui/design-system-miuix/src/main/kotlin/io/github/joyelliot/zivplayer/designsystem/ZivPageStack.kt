// Copyright 2026, compose-miuix-ui contributors
// SPDX-License-Identifier: Apache-2.0
// Adapted from FangFeishu FfPageStack (8676095d). ZivPlayer: explicit route state,
// stable entry identities, physical window corners, input/accessibility isolation.

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import top.yukonga.miuix.kmp.theme.MiuixTheme

/** A full-window stack. Put the complete parent screen (including its bottom bar) in [root]. */
@Composable
fun ZivPageStack(
    state: ZivPageStackState,
    modifier: Modifier = Modifier,
    cornerShape: Shape = rememberZivWindowCorners(),
    root: @Composable () -> Unit,
    page: @Composable (ZivPageEntry) -> Unit,
) {
    val focus = LocalFocusManager.current
    LaunchedEffect(state.entries.lastOrNull()?.id) { focus.clearFocus(force = true) }
    val surface = MiuixTheme.colorScheme.background
    Box(modifier.fillMaxSize().clipToBounds().background(surface)) {
        StackLayer(cornerShape, "page_stack_root", state.canPop,
            coverage = { 1f }, pushed = { when (state.entries.size) { 0 -> 0f; 1 -> state.coverage; else -> 1f } },
            content = root)
        state.entries.forEachIndexed { index, entry ->
            key(entry.id) {
                val top = index == state.entries.lastIndex
                val coverage = { if (top) state.coverage else 1f }
                // Each page owns the scrim below it. Revealing a parent must not undim deeper pages.
                Canvas(Modifier.fillMaxSize()) { drawRect(Color.Black.copy(alpha = 0.45f * coverage())) }
                StackLayer(cornerShape, "page_stack_${entry.route}", !top || state.isTransitionRunning,
                    coverage = coverage,
                    pushed = { if (top) 0f else if (index == state.entries.lastIndex - 1) state.coverage else 1f },
                ) { Box(Modifier.fillMaxSize().background(surface)) { page(entry) } }
            }
        }
        // Block exposed underlay and partially entered pages without adding fake accessibility actions.
        if (state.isTransitionRunning) Box(Modifier.fillMaxSize().blockLayerInput())
    }
}

@Composable
private fun StackLayer(shape: Shape, tag: String, blocked: Boolean,
    coverage: () -> Float, pushed: () -> Float, content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().graphicsLayer {
        val push = pushed().coerceIn(0f, 1f)
        scaleX = 1f - 0.035f * push
        scaleY = scaleX
        translationX = size.width * (1f - coverage().coerceIn(0f, 1f) - 0.08f * push)
        this.shape = shape
        clip = true
    }.testTag(tag)) {
        Box(Modifier.fillMaxSize().then(if (blocked) Modifier.clearAndSetSemantics { } else Modifier)) { content() }
        if (blocked) Box(Modifier.fillMaxSize().blockLayerInput())
    }
}

private fun Modifier.blockLayerInput() = pointerInput(Unit) {
    awaitPointerEventScope { while (true) awaitPointerEvent(PointerEventPass.Initial).changes.forEach { it.consume() } }
}
