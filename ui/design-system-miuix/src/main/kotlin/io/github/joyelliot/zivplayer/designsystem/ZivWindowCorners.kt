// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import android.annotation.SuppressLint
import android.view.RoundedCorner
import android.view.ViewTreeObserver
import androidx.compose.runtime.*
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp

/** Physical corner radii in window pixels; independent of the app's layout direction. */
@Immutable
data class ZivWindowCorners(val topLeft: Float, val topRight: Float, val bottomRight: Float, val bottomLeft: Float) : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline {
        fun radius(value: Float) = value.coerceIn(0f, minOf(size.width, size.height) / 2).let { CornerRadius(it, it) }
        return Outline.Rounded(RoundRect(Rect(0f, 0f, size.width, size.height),
            radius(topLeft), radius(topRight), radius(bottomRight), radius(bottomLeft)))
    }
}

/** Observe layout/insets without replacing Compose's window-insets listener. */
@SuppressLint("DiscouragedApi")
@Composable
fun rememberZivWindowCorners(): ZivWindowCorners {
    val view = LocalView.current
    val fallback = with(LocalDensity.current) { 28.dp.toPx() }
    fun read(): ZivWindowCorners {
        val insets = view.rootWindowInsets
        fun radius(position: Int, resource: String): Float {
            // A missing corner in known insets means this window does not intersect that corner.
            if (insets != null) return insets.getRoundedCorner(position)?.radius?.toFloat() ?: 0f
            val id = view.resources.getIdentifier(resource, "dimen", "android")
            return if (id != 0) view.resources.getDimension(id) else fallback
        }
        return ZivWindowCorners(
            radius(RoundedCorner.POSITION_TOP_LEFT, "rounded_corner_radius_top"),
            radius(RoundedCorner.POSITION_TOP_RIGHT, "rounded_corner_radius_top"),
            radius(RoundedCorner.POSITION_BOTTOM_RIGHT, "rounded_corner_radius_bottom"),
            radius(RoundedCorner.POSITION_BOTTOM_LEFT, "rounded_corner_radius_bottom"),
        )
    }
    var corners by remember(view, fallback) { mutableStateOf(read()) }
    DisposableEffect(view, fallback) {
        val observer = view.viewTreeObserver
        val listener = ViewTreeObserver.OnGlobalLayoutListener { corners = read() }
        var previousInsets = view.rootWindowInsets
        val insetsListener = ViewTreeObserver.OnPreDrawListener {
            val currentInsets = view.rootWindowInsets
            if (currentInsets != previousInsets) { previousInsets = currentInsets; corners = read() }
            true
        }
        observer.addOnGlobalLayoutListener(listener)
        observer.addOnPreDrawListener(insetsListener)
        onDispose { if (observer.isAlive) {
            observer.removeOnGlobalLayoutListener(listener)
            observer.removeOnPreDrawListener(insetsListener)
        } }
    }
    return corners
}
