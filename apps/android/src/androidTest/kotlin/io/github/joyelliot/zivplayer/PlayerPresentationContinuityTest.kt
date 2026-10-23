// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.designsystem.ZivPlaybackSlider
import io.github.joyelliot.zivplayer.designsystem.ZivTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Compile on the host; run later on a dedicated emulator/device, never during a power experiment. */
class PlayerPresentationContinuityTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()

    @Test fun miniExpandedRotatedAndPipSizedLayoutsRetainTheTextureView() {
        val progress = mutableFloatStateOf(0f)
        val rotation = mutableFloatStateOf(0f)
        val pip = mutableStateOf(false)
        lateinit var root: View
        compose.setContent {
            root = LocalView.current.rootView
            BoxWithConstraints(if (pip.value) Modifier.requiredSize(240.dp, 135.dp) else Modifier.fillMaxSize()) {
                val window = IntSize(constraints.maxWidth, constraints.maxHeight)
                val frame = presentationFrame(window.width.toFloat(), window.height.toFloat(), 16f / 9,
                    VideoFrame(50f, window.height - 80f, 56f, 36f), progress.floatValue, rotation.floatValue)
                PersistentVideoStage(null, false, 16f / 9, window, { frame }, { progress.floatValue }, onBounds = {})
            }
        }
        lateinit var texture: TextureView
        compose.runOnIdle { texture = checkNotNull(findTexture(root)) }
        listOf(0.2f to 0f, 0.8f to 35f, 1f to 90f, 0.6f to 40f, 0f to 0f, 1f to 0f).forEach { (p, r) ->
            compose.runOnIdle { progress.floatValue = p; rotation.floatValue = r }
            compose.runOnIdle { assertSame(texture, findTexture(root)) }
        }
        compose.runOnIdle { pip.value = true }
        compose.runOnIdle { assertSame(texture, findTexture(root)) }
        compose.runOnIdle { pip.value = false }
        compose.runOnIdle { assertSame(texture, findTexture(root)) }
    }

    @Test fun aNewDragTakesOverTheCurrentSpringFrame() {
        lateinit var presentation: PlayerPresentationState
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, false) }
            // Observe state so the test clock drives this presentation's frames.
            Box(Modifier.size((10 + presentation.progress * 100).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.runOnIdle { presentation.animateTo(true) }
        compose.mainClock.advanceTimeBy(80)
        var interrupted = 0f
        compose.runOnIdle {
            interrupted = presentation.progress
            assertTrue(interrupted > 0f && interrupted < 1f)
            presentation.beginDrag("video")
            assertEquals(interrupted, presentation.progress, 0f)
            presentation.dragBy(-100f, 1_000f)
            assertEquals((interrupted - 0.1f).coerceAtLeast(0f), presentation.progress, 0.001f)
        }
        compose.mainClock.advanceTimeBy(500)
        compose.runOnIdle { assertEquals((interrupted - 0.1f).coerceAtLeast(0f), presentation.progress, 0.001f) }
    }

    @Test fun videoMotionUpdatesLayersWithoutRecomposingOrMeasuringTheHost() {
        var progress by mutableFloatStateOf(1f)
        var compositions = 0
        var measurements = 0
        lateinit var root: View
        compose.setContent {
            root = LocalView.current.rootView
            SideEffect { compositions++ }
            BoxWithConstraints(Modifier.fillMaxSize().layout { measurable, constraints ->
                measurements++
                val child = measurable.measure(constraints)
                layout(child.width, child.height) { child.place(0, 0) }
            }) {
                val window = IntSize(constraints.maxWidth, constraints.maxHeight)
                PersistentVideoStage(null, false, 16f / 9, window,
                    { presentationFrame(window.width.toFloat(), window.height.toFloat(), 16f / 9,
                        VideoFrame(100f, window.height - 150f, 150f, 90f), progress, 90f) },
                    { progress }, onBounds = {})
            }
        }
        compose.waitForIdle()
        val initialCompositions = compositions
        val initialMeasurements = measurements
        lateinit var texture: TextureView
        compose.runOnIdle { texture = checkNotNull(findTexture(root)) }
        for (p in listOf(0.9f, 0.65f, 0.3f, 0.1f, 0f)) {
            compose.runOnIdle { progress = p }
            compose.waitForIdle()
            compose.runOnIdle {
                assertEquals(initialCompositions, compositions)
                assertEquals(initialMeasurements, measurements)
                assertSame(texture, findTexture(root))
            }
        }
    }

    @Test fun removingAnIdleTimelineDoesNotFinishSomeoneElsesSeekPreview() {
        val visible = mutableStateOf(true)
        var cancelled = 0
        compose.setContent {
            ZivTheme {
                if (visible.value) ZivPlaybackSlider(0.5f, {}, {}, description = "Timeline", onCancel = { cancelled++ })
            }
        }
        compose.runOnIdle { visible.value = false }
        compose.runOnIdle { assertEquals(0, cancelled) }
    }

    @Test fun draggingBackToThePressPositionStillPreviewsAndFinishesOnce() {
        val previews = mutableListOf<Float>()
        var finishes = 0
        var value by mutableFloatStateOf(0.5f)
        compose.setContent {
            ZivTheme {
                ZivPlaybackSlider(value, { value = it }, { finishes++ }, Modifier.width(320.dp),
                    description = "Scrub timeline", onDragValueChange = { previews += it })
            }
        }
        compose.onNodeWithContentDescription("Scrub timeline").performTouchInput {
            down(center)
            advanceEventTime(32)
            moveTo(Offset(width * 0.85f, center.y))
            advanceEventTime(32)
            moveTo(center)
            up()
        }
        compose.runOnIdle {
            assertTrue(previews.size >= 2)
            assertEquals(0.5f, previews.last(), 0.02f)
            assertEquals(1, finishes)
        }
    }

    private fun findTexture(view: View): TextureView? {
        if (view is TextureView) return view
        if (view is ViewGroup) for (index in 0 until view.childCount) findTexture(view.getChildAt(index))?.let { return it }
        return null
    }
}
