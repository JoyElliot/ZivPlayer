// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem.liquid

import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp

/** A small upper card flows into the original large capsule, with no interior seam. */
internal class ConnectedDockShape(private val expanded: Boolean) : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline = with(density) {
        val bottomRadius = minOf(32.dp.toPx(), size.height / 2, size.width / 2)
        if (!expanded) return Outline.Rounded(RoundRect(Rect(0f, 0f, size.width, size.height), CornerRadius(bottomRadius)))
        val inset = minOf(12.dp.toPx(), size.width / 4)
        val topRadius = minOf(24.dp.toPx(), (size.width - inset * 2) / 2)
        val shoulderY = size.height - bottomRadius
        val bend = (shoulderY - topRadius) / 2
        Outline.Generic(Path().apply {
            moveTo(inset + topRadius, 0f)
            lineTo(size.width - inset - topRadius, 0f)
            arcTo(Rect(size.width - inset - topRadius * 2, 0f, size.width - inset, topRadius * 2), 270f, 90f, false)
            cubicTo(size.width - inset, topRadius + bend, size.width, shoulderY - bend, size.width, shoulderY)
            arcTo(Rect(size.width - bottomRadius * 2, size.height - bottomRadius * 2, size.width, size.height), 0f, 90f, false)
            lineTo(bottomRadius, size.height)
            arcTo(Rect(0f, size.height - bottomRadius * 2, bottomRadius * 2, size.height), 90f, 90f, false)
            cubicTo(0f, shoulderY - bend, inset, topRadius + bend, inset, topRadius)
            arcTo(Rect(inset, 0f, inset + topRadius * 2, topRadius * 2), 180f, 90f, false)
            close()
        })
    }
}
