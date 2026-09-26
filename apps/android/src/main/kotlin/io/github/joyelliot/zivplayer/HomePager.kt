// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.PagerState
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.abs

/** One continuous strip, including intermediate pages when navigating from the first to the last. */
@Stable
internal class HomePagerState(val pager: PagerState, private val scope: CoroutineScope, pendingPage: MutableState<Int?>) {
    private var requestedPage by pendingPage
    private var navigation: Job? = null
    private var requestId = 0L
    val selectedPage: Int get() = requestedPage ?: pager.currentPage

    fun navigate(page: Int) {
        require(page in 0 until pager.pageCount)
        val request = ++requestId
        navigation?.cancel()
        if (pager.currentPage == page && abs(pager.currentPageOffsetFraction) < 0.001f && !pager.isScrollInProgress) {
            requestedPage = null
            return
        }
        requestedPage = page
        navigation = scope.launch {
            try {
                // Match the reference project's 300 / 400 / 500 ms page-distance timing.
                val duration = when (abs(page - pager.currentPage)) { 0, 1 -> 300; 2 -> 400; else -> 500 }
                val animation = tween<Float>(duration, easing = FastOutSlowInEasing)
                if (pager.layoutInfo.pageSize == 0) {
                    pager.animateScrollToPage(page, animationSpec = animation)
                } else {
                    // animateScrollToPage may pre-jump over distant pages. Scroll the entire distance instead.
                    pager.scroll {
                        val stride = pager.layoutInfo.pageSize + pager.layoutInfo.pageSpacing
                        val distance = (page - pager.currentPage - pager.currentPageOffsetFraction) * stride
                        var previous = 0f
                        animate(0f, distance, animationSpec = animation) { value, _ ->
                            scrollBy(value - previous)
                            previous = value
                        }
                    }
                    if (pager.currentPage != page || abs(pager.currentPageOffsetFraction) > 0.001f) {
                        pager.scrollToPage(page)
                    }
                }
            } finally {
                // A cancelled animation must never overwrite a newer tap or a finger-driven scroll.
                if (request == requestId) requestedPage = null
            }
        }
    }

    fun snapToPage(page: Int) {
        ++requestId
        navigation?.cancel()
        navigation = null
        requestedPage = null
        // Queue positioning in the next measure; opening/closing the player cannot cancel this with an effect.
        pager.requestScrollToPage(page)
    }

    fun resumePendingNavigation() { if (navigation == null) requestedPage?.let(::navigate) }
}

@Composable
internal fun rememberHomePagerState(): HomePagerState {
    val pager = rememberPagerState { 4 }
    val scope = rememberCoroutineScope()
    val pendingPage = rememberSaveable { mutableStateOf<Int?>(null) }
    val state = remember(pager, scope) { HomePagerState(pager, scope, pendingPage) }
    LaunchedEffect(state) { withFrameNanos { }; state.resumePendingNavigation() }
    return state
}

@Composable
internal fun HomePager(state: HomePagerState, content: @Composable (Int) -> Unit) {
    HorizontalPager(
        state = state.pager,
        modifier = Modifier.fillMaxSize().testTag("home_pager"),
        beyondViewportPageCount = state.pager.pageCount - 1,
        key = { it },
    ) { page ->
        Box(Modifier.fillMaxSize().testTag("home_page_$page")) {
            // Retain offscreen composition and list projections without exposing hidden controls to accessibility.
            Box(Modifier.fillMaxSize().then(
                if (page != state.pager.currentPage) Modifier.clearAndSetSemantics { } else Modifier,
            )) { content(page) }
        }
    }
}
