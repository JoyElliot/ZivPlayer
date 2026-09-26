// Copyright 2026, compose-miuix-ui contributors
// SPDX-License-Identifier: Apache-2.0
// Modified for ZivPlayer in 2026: adapted from FangFeishu commit 8676095d.
// See THIRD_PARTY_NOTICES.md and licenses/Apache-2.0.txt.

package io.github.joyelliot.zivplayer.designsystem.liquid

// Adapted from Kyant0/AndroidLiquidGlass — https://github.com/Kyant0/AndroidLiquidGlass (Apache 2.0).

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.EaseOut
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Alignment.Companion.CenterHorizontally
import androidx.compose.ui.Modifier
import androidx.compose.ui.zIndex
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.dropShadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.testTag
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.widthIn
import io.github.joyelliot.zivplayer.designsystem.ZivNavigationItem
import io.github.joyelliot.zivplayer.designsystem.ZivPlaybackGlyph
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.shadow.Shadow
import androidx.compose.ui.input.pointer.changedToUpIgnoreConsumed
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.util.lerp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.blur.LayerBackdrop
import top.yukonga.miuix.kmp.blur.blur
import top.yukonga.miuix.kmp.blur.drawBackdrop
import top.yukonga.miuix.kmp.blur.highlight.BloomStroke
import top.yukonga.miuix.kmp.blur.highlight.Highlight
import top.yukonga.miuix.kmp.blur.highlight.LightPosition
import top.yukonga.miuix.kmp.blur.highlight.LightSource
import top.yukonga.miuix.kmp.blur.layerBackdrop
import top.yukonga.miuix.kmp.blur.rememberLayerBackdrop
import top.yukonga.miuix.kmp.blur.sensor.rememberDeviceTilt
import top.yukonga.miuix.kmp.theme.LocalContentColor
import top.yukonga.miuix.kmp.theme.MiuixTheme
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sign
import kotlin.math.sin
import kotlin.math.sqrt

private val LocalIosTabScale = staticCompositionLocalOf { { 1f } }

private val iosIndicatorSpecular: Highlight = Highlight(
    width = 1.dp,
    alpha = 1f,
    style = BloomStroke(
        color = Color.White.copy(alpha = 0.12f),
        innerBlurRadius = 2.0.dp,
        primaryLight = LightSource(
            position = LightPosition(0.5f, -0.3f, -0.05f),
            color = Color.White,
            intensity = 1f,
        ),
        secondaryLight = LightSource(
            position = LightPosition(0.5f, 0.8f, -0.5f),
            color = Color.White,
            intensity = 0.4f,
        ),
        dualPeak = true,
    ),
)

// Mirrors HighlightStyle.kt's LIGHT_REF — keep in sync.
private const val LIGHT_REF_X = 0.5f
private const val LIGHT_REF_Y = 0.7f
private const val GRAVITY_DIR_THRESHOLD_SQ = 0.01f // |g_xy| > 0.1, ≈ 6° tilt

/** Rotates a `dualPeak` highlight's primary light using a shared gravity sample. */
private fun gravityRotatedHighlight(
    base: Highlight,
    gravityX: Float,
    gravityY: Float,
    extraDegrees: Float = 0f,
): Highlight {
    val baseStyle = base.style as BloomStroke
    val basePrimary = baseStyle.primaryLight
    val gravityMagnitudeSquared = gravityX * gravityX + gravityY * gravityY
    val (lightX, lightY) = if (gravityMagnitudeSquared > GRAVITY_DIR_THRESHOLD_SQ) {
        val inverseMagnitude = 1f / sqrt(gravityMagnitudeSquared)
        (gravityX * inverseMagnitude) to (gravityY * inverseMagnitude)
    } else {
        0f to -1f
    }
    val radians = extraDegrees * PI / 180.0
    val cosine = cos(radians).toFloat()
    val sine = sin(radians).toFloat()
    val rotatedX = cosine * lightX - sine * lightY
    val rotatedY = sine * lightX + cosine * lightY
    val rotatedPrimary = basePrimary.copy(
        position = LightPosition(
            x = LIGHT_REF_X + rotatedX,
            y = LIGHT_REF_Y + rotatedY,
            z = basePrimary.position.z,
        ),
    )
    return base.copy(style = baseStyle.copy(primaryLight = rotatedPrimary))
}

@Composable
internal fun LiquidGlassNavigationBar(
    items: List<ZivNavigationItem>,
    selectedIndex: Int,
    onItemClick: (Int) -> Unit,
    backdrop: LayerBackdrop?,
    isBlurActive: Boolean,
    modifier: Modifier = Modifier,
    nowPlaying: (@Composable () -> Unit)? = null,
) {
    val isDark = MiuixTheme.colorScheme.background.luminance() < 0.5f
    val pillShape = remember { CircleShape }
    val accentColor = MiuixTheme.colorScheme.primary
    val tabContentColor = MiuixTheme.colorScheme.onSurface
    val surfaceContainer = MiuixTheme.colorScheme.surfaceContainer
    val containerColor = if (isBlurActive) {
        surfaceContainer.copy(alpha = 0.17f)
    } else {
        surfaceContainer.copy(alpha = 0.58f)
    }

    val tabsBackdrop = rememberLayerBackdrop()
    val density = LocalDensity.current
    val isLtr = LocalLayoutDirection.current == LayoutDirection.Ltr
    val animationScope = rememberCoroutineScope()
    val tabsCount = items.size

    var tabWidthPx by remember { mutableFloatStateOf(0f) }
    var totalWidthPx by remember { mutableFloatStateOf(0f) }

    val offsetAnimation = remember { Animatable(0f) }
    val rubberBandPx = with(density) { 4.dp.toPx() }
    val panelOffset by remember(rubberBandPx) {
        derivedStateOf {
            if (totalWidthPx == 0f) {
                0f
            } else {
                val fraction = (offsetAnimation.value / totalWidthPx).coerceIn(-1f, 1f)
                rubberBandPx * fraction.sign * EaseOut.transform(abs(fraction))
            }
        }
    }

    var currentIndex by remember { mutableIntStateOf(selectedIndex) }

    val touchSlopPx = with(density) { 8.dp.toPx() }
    // 长按后才把玻璃滑到手指下；单击不碰玻璃位置，保留原版 animateToValue 切换动画
    val longPressMoveMs = 160L

    fun tabValueUnderFinger(positionX: Float): Float {
        if (tabWidthPx <= 0f) return 0f
        val pad = with(density) { 4.dp.toPx() }
        val localX = if (isLtr) {
            positionX - pad
        } else {
            totalWidthPx - positionX - pad
        }
        // value 以 tab 左缘为 0；玻璃中心对准手指 → 减 0.5 格
        return (localX / tabWidthPx - 0.5f)
            .coerceIn(0f, (tabsCount - 1).toFloat())
    }

    fun tabIndexUnderFinger(positionX: Float): Int {
        if (tabWidthPx <= 0f) return currentIndex
        val pad = with(density) { 4.dp.toPx() }
        val localX = if (isLtr) {
            positionX - pad
        } else {
            totalWidthPx - positionX - pad
        }
        return (localX / tabWidthPx).toInt()
            .coerceIn(0, tabsCount - 1)
    }

    // 仅作动画状态机；整栏手势由下方 barGestureModifier 驱动，避免单击叠两层 press
    val dampedDrag = remember(animationScope, tabsCount) {
        DampedDragAnimation(
            animationScope = animationScope,
            initialValue = selectedIndex.toFloat(),
            valueRange = 0f..(tabsCount - 1).toFloat(),
            visibilityThreshold = 0.001f,
            initialScale = 1f,
            pressedScale = 78f / 56f,
        )
    }

    val onItemClickUpdated by rememberUpdatedState(onItemClick)
    val externalSelection by rememberUpdatedState(selectedIndex)
    fun selectTab(index: Int) {
        currentIndex = index
        dampedDrag.animateToValue(index.toFloat())
        onItemClickUpdated(index)
    }

    val interactiveHighlight = remember(animationScope, isLtr) {
        InteractiveHighlight(
            animationScope = animationScope,
            position = { layerSize, _ ->
                Offset(
                    x = if (isLtr) {
                        (dampedDrag.value + 0.5f) * tabWidthPx + panelOffset
                    } else {
                        layerSize.width - (dampedDrag.value + 0.5f) * tabWidthPx + panelOffset
                    },
                    y = layerSize.height / 2f,
                )
            },
        )
    }

    val barGestureModifier = Modifier.pointerInput(
        dampedDrag,
        selectedIndex,
        tabsCount,
        isLtr,
        touchSlopPx,
        longPressMoveMs,
    ) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            val downX = down.position.x
            interactiveHighlight.press(down.position)
            var didDrag = false
            var engaged = false // 已进入「玻璃跟手」态（长按或拖动）
            var totalDx = 0f
            var completed = false
            val longPressJob = animationScope.launch {
                delay(longPressMoveMs)
                if (!didDrag && tabWidthPx > 0f) {
                    engaged = true
                    dampedDrag.press()
                    dampedDrag.updateValue(tabValueUnderFinger(downX))
                }
            }
            try {
                while (true) {
                    val event = awaitPointerEvent()
                    val change = event.changes.firstOrNull { it.id == down.id } ?: break
                    if (change.isConsumed) break
                    interactiveHighlight.move(change.position)
                    if (change.changedToUpIgnoreConsumed()) {
                        completed = true
                        longPressJob.cancel()
                        val settleIndex = if (didDrag || engaged) {
                            dampedDrag.targetValue.roundToInt().coerceIn(0, tabsCount - 1)
                        } else {
                            // 单击不提前挪动胶囊，由 selectTab 走完整的滑移和缩放。
                            tabIndexUnderFinger(downX)
                        }
                        // 跟手态先松手，再让 animateToValue 完整走一遍 press→滑→release
                        if (engaged || didDrag) {
                            dampedDrag.release()
                        }
                        selectTab(settleIndex)
                        animationScope.launch {
                            offsetAnimation.animateTo(0f, spring(1f, 300f, 0.5f))
                        }
                        break
                    }
                    val dragAmount = change.positionChange()
                    totalDx += dragAmount.x
                    if (!didDrag && abs(totalDx) >= touchSlopPx) {
                        didDrag = true
                        longPressJob.cancel()
                        if (!engaged) {
                            engaged = true
                            dampedDrag.press()
                        }
                    }
                    if (didDrag && tabWidthPx > 0f) {
                        dampedDrag.updateValue(tabValueUnderFinger(change.position.x))
                        animationScope.launch {
                            offsetAnimation.snapTo(offsetAnimation.value + dragAmount.x)
                        }
                        change.consume()
                    }
                }
            } finally {
                longPressJob.cancel()
                interactiveHighlight.release()
                if (!completed) {
                    // A navigation/back action can replace selection while a finger is down.
                    // Restarting this detector must settle to that authoritative destination.
                    currentIndex = externalSelection
                    dampedDrag.animateToValue(currentIndex.toFloat())
                    animationScope.launch { offsetAnimation.animateTo(0f, spring(1f, 300f, 0.5f)) }
                }
            }
        }
    }

    // External page navigation moves the glass without synthesizing a user click.
    LaunchedEffect(selectedIndex) {
        if (currentIndex != selectedIndex) {
            currentIndex = selectedIndex
            dampedDrag.animateToValue(selectedIndex.toFloat())
        }
    }

    // Both highlights share one sensor listener. Reading the State inside drawBackdrop invalidates
    // drawing directly, without recomposing the complete navigation bar for every tilt sample.
    val deviceTiltState = if (isBlurActive && backdrop != null) rememberDeviceTilt() else null
    val baseHighlightState = if (deviceTiltState != null) {
        remember(deviceTiltState) {
            derivedStateOf {
                val tilt = deviceTiltState.value
                gravityRotatedHighlight(
                    base = iosIndicatorSpecular,
                    gravityX = tilt.gravityX,
                    gravityY = tilt.gravityY,
                    extraDegrees = -45f,
                )
            }
        }
    } else {
        null
    }
    val pillHighlightState = if (deviceTiltState != null) {
        remember(deviceTiltState) {
            derivedStateOf {
                val tilt = deviceTiltState.value
                gravityRotatedHighlight(
                    base = iosIndicatorSpecular,
                    gravityX = tilt.gravityX,
                    gravityY = tilt.gravityY,
                    extraDegrees = 90f,
                )
            }
        }
    } else {
        null
    }

    val combinedBackdrop = backdrop?.let { rememberCombinedBackdrop(it, tabsBackdrop) }

    val tabsContent: @Composable RowScope.(Boolean) -> Unit = { filledIcons ->
        val tabScale = LocalIosTabScale.current
        items.forEachIndexed { index, item ->
            Column(
                modifier = Modifier
                    .then(if (!filledIcons) Modifier
                        .clickable(interactionSource = null, indication = null, role = Role.Tab,
                            onClick = { selectTab(index) })
                        .semantics { selected = index == currentIndex }
                        .testTag(item.testTag)
                    else Modifier)
                    .weight(1f)
                    .fillMaxHeight()
                    .graphicsLayer {
                        val s = tabScale()
                        scaleX = s
                        scaleY = s
                    },
                verticalArrangement = Arrangement.spacedBy(1.dp, Alignment.CenterVertically),
                horizontalAlignment = CenterHorizontally,
            ) {
                ZivPlaybackGlyph(item.icon, Modifier.size(22.dp), color = LocalContentColor.current)
                Text(
                    text = item.label,
                    color = LocalContentColor.current,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Normal,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }

    // Host has consumed the system inset. Preserve the reference's extra clearance
    // so the velocity-stretched capsule can expand past the track without clipping.
    val bottomClearance = if (WindowInsets.navigationBars.getBottom(density) > 0) 10.dp else 26.dp
    Box(modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        Column(
            Modifier.padding(start = 32.dp, end = 32.dp, top = 8.dp, bottom = bottomClearance)
                .widthIn(max = 480.dp).fillMaxWidth().testTag("app_playback_dock"),
            verticalArrangement = Arrangement.spacedBy((-8).dp),
        ) {
            nowPlaying?.let { content ->
                // The smaller card overlaps the back of the original navigation capsule.
                Box(Modifier.fillMaxWidth().padding(horizontal = 16.dp).zIndex(1f)
                    .graphicsLayer { translationX = panelOffset }) { content() }
            }
            Box(Modifier.fillMaxWidth().testTag("app_bottom_navigation"),
                contentAlignment = Alignment.CenterStart) {
                CompositionLocalProvider(LocalContentColor provides tabContentColor) {
                    Row(
                        modifier = Modifier
                            .selectableGroup()
                            .onSizeChanged { coords ->
                                totalWidthPx = coords.width.toFloat()
                                val contentWidthPx = totalWidthPx - with(density) { 8.dp.toPx() }
                                tabWidthPx = (contentWidthPx / tabsCount).coerceAtLeast(0f)
                            }
                            .graphicsLayer {
                                clip = false
                                translationX = panelOffset
                            }
                            .dropShadow(
                                shape = pillShape,
                                shadow = Shadow(
                                    radius = 8.dp,
                                    color = Color.Black,
                                    alpha = 0.14f,
                                ),
                            )
                            .then(
                                if (isBlurActive && backdrop != null) {
                                    Modifier.drawBackdrop(
                                        backdrop = backdrop,
                                        shape = { pillShape },
                                        effects = {
                                            padding = maxOf(padding, 28.dp.toPx())
                                            vibrancy()
                                            blur(
                                                4.dp.toPx(),
                                                4.dp.toPx(),
                                            )
                                            lens(
                                                refractionHeight = 28.dp.toPx(),
                                                refractionAmount = 28.dp.toPx(),
                                            )
                                        },
                                        highlight = { baseHighlightState?.value?.copy(alpha = 0.75f) },
                                        layerBlock = {
                                            val width = size.width.coerceAtLeast(1f)
                                            val pressScale = lerp(
                                                1f,
                                                1f + 14.dp.toPx() / width,
                                                dampedDrag.pressProgress,
                                            )
                                            scaleX = pressScale
                                            scaleY = pressScale
                                        },
                                        onDrawSurface = { drawRect(containerColor) },
                                    )
                                } else {
                                    Modifier
                                        .background(containerColor, pillShape)
                                },
                            )
                            .then(if (isBlurActive) interactiveHighlight.modifier else Modifier)
                            .height(64.dp)
                            .padding(4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        content = { tabsContent(false) },
                    )
                }

                if (isBlurActive && backdrop != null) {
                    CompositionLocalProvider(
                        LocalIosTabScale provides { lerp(1f, 1.18f, dampedDrag.pressProgress) },
                        LocalContentColor provides accentColor,
                    ) {
                        Row(
                            modifier = Modifier
                                .clearAndSetSemantics {}
                                .alpha(0f)
                                .layerBackdrop(tabsBackdrop)
                                .graphicsLayer { translationX = panelOffset }
                                .drawBackdrop(
                                    backdrop = backdrop,
                                    shape = { pillShape },
                                    effects = {
                                        padding = maxOf(padding, 28.dp.toPx())
                                        vibrancy()
                                        blur(4.dp.toPx(), 4.dp.toPx())
                                        lens(
                                            refractionHeight = 28.dp.toPx(),
                                            refractionAmount = 28.dp.toPx(),
                                        )
                                    },
                                    onDrawSurface = { drawRect(containerColor) },
                                )
                                .then(interactiveHighlight.modifier)
                                .height(56.dp)
                                .padding(horizontal = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            content = { tabsContent(true) },
                        )
                    }
                }

                if (tabWidthPx > 0f) {
                    val tabWidthDp = with(density) { tabWidthPx.toDp() }
                    if (isBlurActive && combinedBackdrop != null) {
                        // 仅视觉层：手势改由整条底栏透明层接管，避免必须点中玻璃才能拖
                        Box(
                            modifier = Modifier
                                .padding(horizontal = 4.dp)
                                .graphicsLayer {
                                    clip = false
                                    val singleTabWidth = tabWidthPx
                                    val progressOffset = dampedDrag.value * singleTabWidth
                                    translationX = if (isLtr) progressOffset + panelOffset else -progressOffset + panelOffset
                                }
                                .drawBackdrop(
                                    backdrop = combinedBackdrop,
                                    shape = { pillShape },
                                    effects = {
                                        val progress = dampedDrag.pressProgress
                                        lens(
                                            refractionHeight = 12.dp.toPx() * progress,
                                            refractionAmount = 16.dp.toPx() * progress,
                                            depthEffect = true,
                                            chromaticAberration = 0.5f,
                                        )
                                    },
                                    highlight = {
                                        pillHighlightState?.value?.copy(alpha = dampedDrag.pressProgress)
                                    },
                                    layerBlock = {
                                        val progress = dampedDrag.pressProgress
                                        val capsuleWidth = size.width.coerceAtLeast(1f)
                                        val extraScale = 1f + 3.dp.toPx() * progress / capsuleWidth
                                        scaleX = dampedDrag.scaleX * extraScale
                                        scaleY = dampedDrag.scaleY * extraScale
                                        val velocity = dampedDrag.velocity / 10f
                                        scaleX /= 1f - (velocity * 0.75f).coerceIn(-0.2f, 0.2f)
                                        scaleY *= 1f - (velocity * 0.25f).coerceIn(-0.2f, 0.2f)
                                    },
                                    onDrawSurface = {
                                        val progress = dampedDrag.pressProgress
                                        drawRect(
                                            color = if (!isDark) Color.Black.copy(alpha = 0.1f) else Color.White.copy(alpha = 0.1f),
                                            alpha = 1f - progress,
                                        )
                                        drawRect(Color.Black.copy(alpha = 0.03f * progress))
                                    },
                                )
                                .innerShadow(shape = pillShape) {
                                    val progress = dampedDrag.pressProgress
                                    if (progress <= 0f) {
                                        null
                                    } else {
                                        InnerShadow(
                                            radius = 8.dp * progress,
                                            color = Color.Black.copy(alpha = 0.15f),
                                            alpha = progress,
                                        )
                                    }
                                }
                                .testTag("navigation_indicator")
                                .height(56.dp)
                                .width(tabWidthDp),
                        )
                    } else {
                        Box(
                            modifier = Modifier
                                .padding(horizontal = 4.dp)
                                .graphicsLayer {
                                    val progressOffset = dampedDrag.value * tabWidthPx
                                    translationX = if (isLtr) progressOffset + panelOffset else -progressOffset + panelOffset
                                }
                                .clip(pillShape)
                                .background(accentColor.copy(alpha = 0.15f), pillShape)
                                .height(56.dp)
                                .width(tabWidthDp),
                            contentAlignment = Alignment.CenterStart,
                        ) {
                            CompositionLocalProvider(LocalContentColor provides accentColor) {
                                Row(
                                    modifier = Modifier
                                        .clearAndSetSemantics {}
                                        .wrapContentWidth(align = Alignment.Start, unbounded = true)
                                        .requiredWidth(with(density) { (totalWidthPx - 8.dp.toPx()).toDp() })
                                        .height(56.dp)
                                        .graphicsLayer {
                                            val progressOffset = dampedDrag.value * tabWidthPx
                                            translationX = if (isLtr) -progressOffset else progressOffset
                                        },
                                    verticalAlignment = Alignment.CenterVertically,
                                    content = { tabsContent(true) },
                                )
                            }
                        }
                    }
                }

                // 整条底栏手势层：单击与无障碍点击共用 selectTab；
                // 长按 / 横向滑动才让玻璃跟到手指下（不抢单击动画）
                Box(
                    modifier = Modifier
                        .matchParentSize()
                        .then(barGestureModifier),
                )
            }
        }
    }
}
