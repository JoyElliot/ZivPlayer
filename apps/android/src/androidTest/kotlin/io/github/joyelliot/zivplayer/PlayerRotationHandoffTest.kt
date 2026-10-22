// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Host compilation only this round. These clock-driven tests do not request device orientation. */
class PlayerRotationHandoffTest {
    @get:Rule val compose = createComposeRule()

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

    @Test fun landscapeDownwardGestureRequestsRestorationWithoutRevealingTheHomePage() {
        lateinit var presentation: PlayerPresentationState
        var restores = 0
        var settles = 0
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, true) }
            Box(Modifier.size(300.dp).testTag("handle").playerExpansionDrag(presentation, 500f,
                { settles++ }, "handle", interceptDownwardDrag = { restores++; true }))
        }
        compose.onNodeWithTag("handle").performTouchInput {
            down(center)
            moveTo(center + Offset(0f, 80f), 100)
            up()
        }
        compose.runOnIdle {
            assertEquals(1, restores)
            assertEquals(0, settles)
            assertEquals(1f, presentation.progress, 0f)
            assertFalse(presentation.dragging)
        }
    }

    @Test fun upwardThenDownwardGestureCanStillTransferToDirectionRestoration() {
        lateinit var presentation: PlayerPresentationState
        var restores = 0
        compose.setContent {
            val scope = rememberCoroutineScope()
            presentation = remember { PlayerPresentationState(scope, true) }
            Box(Modifier.size(300.dp).testTag("handle").playerExpansionDrag(presentation, 500f,
                {}, "handle", interceptDownwardDrag = { restores++; true }))
        }
        compose.onNodeWithTag("handle").performTouchInput {
            down(center)
            moveTo(center + Offset(0f, -70f), 100)
            moveTo(center + Offset(0f, 80f), 100)
            up()
        }
        compose.runOnIdle {
            assertEquals(1, restores)
            assertEquals(1f, presentation.progress, 0f)
            assertFalse(presentation.dragging)
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
}
