// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Clock-driven animation contracts; real window rotation is covered by the device playback test. */
class PlayerRotationHandoffTest {
    @get:Rule(order = 0) val foreground = DeviceForegroundRule()
    @get:Rule(order = 1) val compose = createComposeRule()

    @Test fun reversingAnAnimationCannotSubmitTheOldOrientation() {
        var turned by mutableStateOf(false)
        var angle = 0f
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false),
                PlayerCanvasGeometry(400, 800, 0), false, false) { turn, pip -> requests += turn to pip }
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); turned = true }
        compose.mainClock.advanceTimeBy(100)
        compose.runOnIdle { assertTrue(angle > 0f && angle < 90f); turned = false }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle {
            assertEquals(0f, angle, 0.001f)
            assertEquals(listOf(false to false), requests)
        }
    }

    @Test fun windowAcknowledgementCancelsTheTransformWithoutStartingAnotherAnimation() {
        var turned by mutableStateOf(false)
        var canvas by mutableStateOf(PlayerCanvasGeometry(400, 800, 0))
        var effectiveAngle = 0f
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            val angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false), canvas,
                false, false) { turn, pip -> requests += turn to pip }
            effectiveAngle = angle + canvas.compensation
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); turned = true }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(listOf(true to false), requests); canvas = PlayerCanvasGeometry(800, 400, 1) }
        compose.mainClock.advanceTimeByFrame()
        compose.runOnIdle { assertEquals(0f, effectiveAngle, 0.001f) }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(listOf(true to false), requests) }
    }

    @Test fun pipInterruptsPendingRotationAndKeepsItsWindowUpright() {
        var turned by mutableStateOf(false)
        var transition by mutableStateOf(false)
        var pip by mutableStateOf(false)
        var angle = 0f
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false),
                PlayerCanvasGeometry(400, 800, 0), pip, transition) { turn, p -> requests += turn to p }
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); turned = true }
        compose.mainClock.advanceTimeBy(100)
        compose.runOnIdle { transition = true }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle {
            assertEquals(listOf(true to true), requests)
            assertEquals(0f, angle, 0.001f)
            pip = true; transition = false
        }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(0f, angle, 0.001f); assertEquals(1, requests.size) }
    }

    @Test fun backgroundCancelsDispatchAndForegroundResumesTheLatestIntent() {
        var active by mutableStateOf(true)
        var turned by mutableStateOf(false)
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            val angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false),
                PlayerCanvasGeometry(400, 800, 0), false, false, active) { turn, p -> requests += turn to p }
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); turned = true }
        compose.mainClock.advanceTimeBy(80)
        compose.runOnIdle { active = false }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertTrue(requests.isEmpty()); active = true }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(listOf(true to false), requests) }
    }

    @Test fun landscapeDownwardGestureShrinksAndRotatesInTheSameFrame() {
        lateinit var presentation: PlayerPresentationState
        var frame = VideoFrame(0f, 0f, 0f, 0f)
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, true) }
            frame = presentationFrame(400f, 800f, 16f / 9, VideoFrame(60f, 740f, 56f, 32f), presentation.progress, 90f)
            Box(Modifier.size(300.dp).testTag("handle").playerExpansionDrag(presentation, 500f,
                {}, "handle"))
        }
        compose.mainClock.autoAdvance = false
        val initialWidth = frame.width
        compose.onNodeWithTag("handle").performTouchInput {
            down(center)
            moveTo(center + Offset(0f, 80f), 100)
        }
        compose.mainClock.advanceTimeByFrame()
        compose.runOnIdle {
            assertTrue(presentation.dragging)
            assertTrue(presentation.progress < 1f)
            assertTrue(frame.width < initialWidth)
            assertTrue(frame.rotation > 0f && frame.rotation < 90f)
        }
        compose.onNodeWithTag("handle").performTouchInput { up() }
    }

    @Test fun aCollapseGestureCanReverseWithoutWaitingForSystemRotation() {
        lateinit var presentation: PlayerPresentationState
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, true) }
            Box(Modifier.size(300.dp).testTag("handle").playerExpansionDrag(presentation, 500f,
                {}, "handle"))
        }
        compose.mainClock.autoAdvance = false
        compose.onNodeWithTag("handle").performTouchInput {
            down(center)
            moveTo(center + Offset(0f, 80f), 100)
        }
        val partial = presentation.progress
        compose.onNodeWithTag("handle").performTouchInput { moveTo(center + Offset(0f, 20f), 100) }
        compose.runOnIdle { assertTrue(presentation.progress > partial) }
        compose.onNodeWithTag("handle").performTouchInput { up() }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle {
            assertEquals(1f, presentation.progress, 0f)
            assertFalse(presentation.dragging)
        }
    }

    @Test fun reachingMiniRestoresTheWindowWithoutAnotherRotationSpring() {
        var turned by mutableStateOf(true)
        var collapsed by mutableStateOf(false)
        var angle = 90f
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false), PlayerCanvasGeometry(800, 400, 1),
                false, false, collapsed = collapsed) { turn, pip -> requests += turn to pip }
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); collapsed = true; turned = false }
        compose.mainClock.advanceTimeBy(64)
        compose.runOnIdle {
            assertEquals(0f, angle, 0f)
            assertEquals(listOf(false to false), requests)
        }
    }

    @Test fun closingDuringRotationCancelsTheOldWindowRequest() {
        var turned by mutableStateOf(false)
        var closing by mutableStateOf(false)
        var collapsed by mutableStateOf(false)
        var angle = 0f
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false), PlayerCanvasGeometry(400, 800, 0),
                false, false, collapsed = collapsed, collapsing = closing,
                allowWindowRequest = !closing || collapsed) { turn, pip -> requests += turn to pip }
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); turned = true }
        compose.mainClock.advanceTimeBy(100)
        compose.runOnIdle { assertTrue(angle > 0f && angle < 90f); closing = true }
        compose.mainClock.advanceTimeByFrame()
        val frozen = angle
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(frozen, angle, 0f); assertTrue(requests.isEmpty()); collapsed = true; turned = false }
        compose.mainClock.advanceTimeBy(64)
        compose.runOnIdle { assertEquals(listOf(false to false), requests); assertEquals(0f, angle, 0f) }
    }

    @Test fun anOpeningRotationWaitsForPresentationBeforeChangingTheWindow() {
        var ready by mutableStateOf(false)
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            rememberPlayerRotation(true, PlayerRotationBasis(0, false), PlayerCanvasGeometry(400, 800, 0),
                false, false, allowWindowRequest = ready) { turn, pip -> requests += turn to pip }
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertTrue(requests.isEmpty()); ready = true }
        compose.mainClock.advanceTimeBy(64)
        compose.runOnIdle { assertEquals(listOf(true to false), requests) }
    }

    @Test fun draggingUsesTheScreenAxisUnderRotatedAncestors() {
        var rotation by mutableFloatStateOf(0f)
        lateinit var presentation: PlayerPresentationState
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, true) }
            Box(Modifier.size(300.dp).testTag("screen")) {
                Box(Modifier.fillMaxSize().graphicsLayer { rotationZ = rotation }
                    .playerExpansionDrag(presentation, 500f, {}))
            }
        }
        compose.mainClock.autoAdvance = false
        for (angle in listOf(0f, -90f, 90f, 45f)) {
            compose.runOnIdle { rotation = angle; presentation.animateTo(true) }
            compose.mainClock.advanceTimeBy(2_000)
            compose.onNodeWithTag("screen").performTouchInput {
                down(center); moveTo(center + Offset(80f, 0f), 100); up()
            }
            compose.runOnIdle { assertEquals(1f, presentation.progress, 0f); assertFalse(presentation.dragging) }
            compose.onNodeWithTag("screen").performTouchInput {
                down(center); moveTo(center + Offset(0f, 80f), 100)
            }
            val partial = presentation.progress
            compose.runOnIdle { assertTrue(presentation.dragging); assertTrue(partial < 1f) }
            compose.onNodeWithTag("screen").performTouchInput { moveTo(center + Offset(0f, 20f), 100) }
            compose.runOnIdle { assertTrue(presentation.progress > partial) }
            compose.onNodeWithTag("screen").performTouchInput { up() }
            compose.mainClock.advanceTimeBy(2_000)
            compose.runOnIdle { assertEquals(1f, presentation.progress, 0f) }
        }
    }

    @Test fun homeDuringRotationAlignsImmediatelyEvenWhenTheForegroundClockStops() {
        var turned by mutableStateOf(false)
        var active by mutableStateOf(true)
        var transition by mutableStateOf(false)
        var immediate by mutableStateOf(false)
        var pip by mutableStateOf(false)
        var angle = 0f
        val requests = mutableListOf<Pair<Boolean, Boolean>>()
        compose.setContent {
            angle = rememberPlayerRotation(turned, PlayerRotationBasis(0, false),
                PlayerCanvasGeometry(400, 800, 0), pip, transition, active, immediate) { turn, p -> requests += turn to p }
            Box(Modifier.size((100 + angle).dp))
        }
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { requests.clear(); turned = true }
        compose.mainClock.advanceTimeBy(100)
        compose.runOnIdle { active = false; transition = true; immediate = true }
        compose.mainClock.advanceTimeByFrame()
        compose.runOnIdle {
            assertEquals(0f, angle, 0.001f)
            assertTrue(requests.isEmpty()) // Activity already submitted PiP synchronously.
            pip = true; transition = false; immediate = false
        }
        compose.mainClock.advanceTimeByFrame()
        compose.runOnIdle { assertEquals(0f, angle, 0.001f); pip = false; active = true }
        compose.mainClock.advanceTimeByFrame()
        compose.runOnIdle { assertEquals(90f, angle, 0.001f) }
    }

    @Test fun cancellationSettlesOnceAndAButtonCanTakeOverTheFinger() {
        lateinit var presentation: PlayerPresentationState
        val settlements = mutableListOf<Boolean>()
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, true) }
            Box(Modifier.size(300.dp).testTag("handle")
                .playerExpansionDrag(presentation, 500f, { settlements += it }, "handle"))
        }
        compose.mainClock.autoAdvance = false
        compose.onNodeWithTag("handle").performTouchInput {
            down(center); moveTo(center + Offset(0f, 80f), 100); cancel()
        }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle { assertEquals(listOf(true), settlements); assertEquals(1f, presentation.progress, 0f) }
        compose.onNodeWithTag("handle").performTouchInput {
            down(center); moveTo(center + Offset(0f, 80f), 100)
        }
        compose.runOnIdle { assertTrue(presentation.dragging); presentation.animateTo(false) }
        compose.onNodeWithTag("handle").performTouchInput { moveTo(center + Offset(0f, 20f), 100); up() }
        compose.mainClock.advanceTimeBy(2_000)
        compose.runOnIdle {
            assertEquals(listOf(true), settlements)
            assertEquals(0f, presentation.progress, 0f)
            assertFalse(presentation.dragging)
        }
    }
}
