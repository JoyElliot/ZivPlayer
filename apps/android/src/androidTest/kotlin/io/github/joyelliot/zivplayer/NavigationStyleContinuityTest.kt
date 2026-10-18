// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Compiled on the host; execution requires a separately authorized device session. */
class NavigationStyleContinuityTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()

    @Test fun switchingDocksAndExpandingUsesTheSameVideoViewAndTheCurrentSlot() {
        val style = mutableStateOf(ZivNavigationStyle.LIQUID_GLASS)
        val progress = mutableFloatStateOf(0f)
        var anchor: VideoFrame? by mutableStateOf(null)
        lateinit var root: View
        var rendered: VideoFrame? = null
        compose.setContent {
            root = LocalView.current.rootView
            ZivTheme {
                BoxWithConstraints(Modifier.fillMaxSize()) {
                    val window = IntSize(constraints.maxWidth, constraints.maxHeight)
                    ZivHomeScaffold(style.value, bottomBar = {
                        ZivNavigationBar(listOf(
                            ZivNavigationItem("Video", ZivPlaybackIcon.PLAY, "test_video"),
                            ZivNavigationItem("Audio", ZivPlaybackIcon.AUDIO, "test_audio"),
                            ZivNavigationItem("Queue", ZivPlaybackIcon.LIST, "test_queue"),
                            ZivNavigationItem("Settings", ZivPlaybackIcon.SETTINGS, "test_settings"),
                        ), 0, {}, nowPlaying = {
                            Box(Modifier.fillMaxWidth().height(48.dp)) {
                                MiniVideoAnchor(style.value) { anchor = it }
                            }
                        })
                    }) { }
                    val frame = presentationFrame(window.width.toFloat(), window.height.toFloat(), 16f / 9,
                        anchor ?: VideoFrame(0f, 0f, 56f, 32f), progress.floatValue, 0f)
                    SideEffect { rendered = frame }
                    PersistentVideoStage(null, false, 16f / 9, window, frame, progress.floatValue,
                        miniCornerRadius = if (style.value == ZivNavigationStyle.LIQUID_GLASS) 12.dp else 8.dp,
                        onBounds = {})
                }
            }
        }
        compose.waitForIdle()
        lateinit var texture: TextureView
        compose.runOnIdle { texture = checkNotNull(findTexture(root)) }
        for (next in listOf(ZivNavigationStyle.LIQUID_GLASS, ZivNavigationStyle.SIMPLE, ZivNavigationStyle.LIQUID_GLASS)) {
            compose.runOnIdle { style.value = next; progress.floatValue = 0f }
            compose.waitForIdle()
            val slot = compose.onNodeWithTag("mini_video_anchor").fetchSemanticsNode().boundsInRoot
            compose.runOnIdle {
                val frame = checkNotNull(rendered)
                assertEquals(slot.center.x, frame.centerX, 1f)
                assertEquals(slot.center.y, frame.centerY, 1f)
                assertTrue(frame.width <= slot.width + 1f && frame.height <= slot.height + 1f)
                assertSame(texture, findTexture(root))
            }
            for (p in listOf(0.3f, 1f, 0.7f, 0f)) {
                compose.runOnIdle { progress.floatValue = p }
                compose.runOnIdle { assertSame(texture, findTexture(root)) }
            }
        }
    }

    @Test fun videoSlotTracksAncestorSpringTransformsWithoutChangingItsLayoutSize() {
        val scale = mutableFloatStateOf(1f)
        val offset = mutableFloatStateOf(0f)
        var anchor: VideoFrame? = null
        compose.setContent {
            Box(Modifier.fillMaxSize()) {
                Box(Modifier.size(240.dp, 80.dp).graphicsLayer {
                    scaleX = scale.floatValue
                    scaleY = scale.floatValue
                    translationX = offset.floatValue
                }) {
                    MiniVideoAnchor(ZivNavigationStyle.LIQUID_GLASS) { anchor = it }
                }
            }
        }
        compose.waitForIdle()
        lateinit var initial: VideoFrame
        compose.runOnIdle { initial = checkNotNull(anchor) }
        compose.runOnIdle { scale.floatValue = 1.12f; offset.floatValue = 20f }
        compose.waitForIdle()
        val transformed = compose.onNodeWithTag("mini_video_anchor").fetchSemanticsNode().boundsInRoot
        compose.runOnIdle {
            val actual = checkNotNull(anchor)
            assertEquals(initial.width * 1.12f, actual.width, 2f)
            assertEquals(initial.height * 1.12f, actual.height, 2f)
            assertEquals(transformed.center.x, actual.centerX, 1f)
            assertEquals(transformed.center.y, actual.centerY, 1f)
        }
        compose.runOnIdle { scale.floatValue = 1f; offset.floatValue = 0f }
        compose.waitForIdle()
        compose.runOnIdle { assertEquals(initial, anchor) }
    }

    private fun findTexture(view: View): TextureView? {
        if (view is TextureView) return view
        if (view is ViewGroup) for (index in 0 until view.childCount) findTexture(view.getChildAt(index))?.let { return it }
        return null
    }
}
