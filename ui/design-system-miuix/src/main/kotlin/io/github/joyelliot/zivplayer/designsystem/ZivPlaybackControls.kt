// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

enum class ZivPlaybackIcon { PLAY, PAUSE, BACK, MORE, PREVIOUS, NEXT, LIST, FULLSCREEN, FOLDER, CLOSE, CHECK, AUDIO, SUBTITLES, LOCK, UNLOCK, FIT, SPEED, REPEAT, VOLUME, SEARCH, SORT, SETTINGS, HISTORY, ADD, PIP }

/** Small source-owned icons keep playback controls independent of a second UI toolkit. */
@Composable
fun ZivPlaybackIconButton(
    icon: ZivPlaybackIcon,
    description: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    prominent: Boolean = false,
    color: Color = Color.White,
) {
    Box(
        modifier.size(if (prominent) 56.dp else 48.dp).clip(CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .semantics { contentDescription = description },
        contentAlignment = Alignment.Center,
    ) {
        ZivPlaybackGlyph(icon, Modifier.size(if (prominent) 32.dp else 24.dp), enabled, color)
    }
}

@Composable
fun ZivPlaybackGlyph(icon: ZivPlaybackIcon, modifier: Modifier = Modifier.size(24.dp), enabled: Boolean = true, color: Color = Color.White) {
    Canvas(modifier) {
        val ink = color.copy(alpha = if (enabled) color.alpha else color.alpha * 0.35f)
        val unit = size.minDimension / 24f
        fun line(x1: Float, y1: Float, x2: Float, y2: Float) = drawLine(
            ink, Offset(x1 * unit, y1 * unit), Offset(x2 * unit, y2 * unit), 1.8f * unit, StrokeCap.Round,
        )
        fun play(left: Float, right: Float) {
            drawPath(Path().apply {
                moveTo(left * unit, 5f * unit); lineTo(right * unit, 12f * unit)
                lineTo(left * unit, 19f * unit); close()
            }, ink)
        }
        when (icon) {
            ZivPlaybackIcon.SEARCH -> {
                drawCircle(ink, 7f * unit, Offset(10f * unit, 10f * unit), style = Stroke(1.8f * unit))
                line(15f, 15f, 21f, 21f)
            }
            ZivPlaybackIcon.SORT -> { line(4f, 6f, 20f, 6f); line(4f, 12f, 15f, 12f); line(4f, 18f, 10f, 18f) }
            ZivPlaybackIcon.ADD -> { line(12f, 4f, 12f, 20f); line(4f, 12f, 20f, 12f) }
            ZivPlaybackIcon.SETTINGS -> {
                listOf(6f, 12f, 18f).forEach { line(3f, it, 21f, it) }
                listOf(Offset(8f, 6f), Offset(16f, 12f), Offset(10f, 18f)).forEach {
                    drawCircle(ink, 2.5f * unit, it * unit)
                }
            }
            ZivPlaybackIcon.HISTORY -> {
                drawArc(ink, -90f, 300f, false, Offset(3f * unit, 3f * unit), Size(18f * unit, 18f * unit), style = Stroke(1.8f * unit))
                line(12f, 7f, 12f, 12f); line(12f, 12f, 16f, 14f)
            }
            ZivPlaybackIcon.PIP -> {
                drawRoundRect(ink, Offset(2f * unit, 4f * unit), Size(20f * unit, 16f * unit), style = Stroke(1.8f * unit))
                drawRect(ink, Offset(12f * unit, 11f * unit), Size(7f * unit, 6f * unit))
            }
            ZivPlaybackIcon.PLAY -> play(8f, 19f)
            ZivPlaybackIcon.PAUSE -> {
                drawRoundRect(ink, Offset(6f * unit, 5f * unit), Size(4f * unit, 14f * unit))
                drawRoundRect(ink, Offset(14f * unit, 5f * unit), Size(4f * unit, 14f * unit))
            }
            ZivPlaybackIcon.BACK -> { line(10f, 5f, 3f, 12f); line(3f, 12f, 10f, 19f); line(3f, 12f, 21f, 12f) }
            ZivPlaybackIcon.MORE -> listOf(5f, 12f, 19f).forEach { drawCircle(ink, 1.6f * unit, Offset(12f * unit, it * unit)) }
            ZivPlaybackIcon.PREVIOUS -> { play(17f, 7f); line(5f, 5f, 5f, 19f) }
            ZivPlaybackIcon.NEXT -> { play(7f, 17f); line(19f, 5f, 19f, 19f) }
            ZivPlaybackIcon.LIST -> listOf(6f, 12f, 18f).forEach { line(8f, it, 21f, it); drawCircle(ink, unit, Offset(3f * unit, it * unit)) }
            ZivPlaybackIcon.FULLSCREEN -> {
                line(4f, 9f, 4f, 4f); line(4f, 4f, 9f, 4f); line(15f, 4f, 20f, 4f); line(20f, 4f, 20f, 9f)
                line(4f, 15f, 4f, 20f); line(4f, 20f, 9f, 20f); line(15f, 20f, 20f, 20f); line(20f, 20f, 20f, 15f)
            }
            ZivPlaybackIcon.FOLDER -> drawPath(Path().apply {
                moveTo(3f * unit, 6f * unit); lineTo(10f * unit, 6f * unit); lineTo(12f * unit, 9f * unit)
                lineTo(21f * unit, 9f * unit); lineTo(21f * unit, 19f * unit); lineTo(3f * unit, 19f * unit); close()
            }, ink, style = Stroke(1.8f * unit))
            ZivPlaybackIcon.CLOSE -> { line(6f, 6f, 18f, 18f); line(18f, 6f, 6f, 18f) }
            ZivPlaybackIcon.CHECK -> { line(4f, 12f, 9f, 17f); line(9f, 17f, 20f, 6f) }
            ZivPlaybackIcon.AUDIO -> {
                line(15f, 4f, 15f, 16f); line(15f, 4f, 20f, 3f)
                drawOval(ink, Offset(6f * unit, 14f * unit), Size(9f * unit, 7f * unit))
            }
            ZivPlaybackIcon.SUBTITLES -> {
                drawRoundRect(ink, Offset(3f * unit, 5f * unit), Size(18f * unit, 14f * unit), style = Stroke(1.8f * unit))
                line(6f, 11f, 10f, 11f); line(13f, 11f, 18f, 11f)
                line(6f, 15f, 14f, 15f); line(17f, 15f, 18f, 15f)
            }
            ZivPlaybackIcon.LOCK, ZivPlaybackIcon.UNLOCK -> {
                drawRoundRect(ink, Offset(5f * unit, 11f * unit), Size(14f * unit, 10f * unit), style = Stroke(1.8f * unit))
                drawPath(Path().apply {
                    moveTo(8f * unit, 11f * unit); lineTo(8f * unit, 7f * unit)
                    cubicTo(8f * unit, 1f * unit, 16f * unit, 1f * unit, 16f * unit, 7f * unit)
                    if (icon == ZivPlaybackIcon.LOCK) lineTo(16f * unit, 11f * unit)
                }, ink, style = Stroke(1.8f * unit))
                drawCircle(ink, 1.5f * unit, Offset(12f * unit, 16f * unit))
            }
            ZivPlaybackIcon.FIT -> {
                drawRoundRect(ink, Offset(2f * unit, 5f * unit), Size(20f * unit, 14f * unit), style = Stroke(1.8f * unit))
                line(7f, 6f, 7f, 18f); line(17f, 6f, 17f, 18f)
            }
            ZivPlaybackIcon.SPEED -> {
                drawArc(ink, 145f, 250f, false, Offset(3f * unit, 3f * unit), Size(18f * unit, 18f * unit), style = Stroke(1.8f * unit, cap = StrokeCap.Round))
                line(12f, 13f, 17f, 7f); drawCircle(ink, 2f * unit, Offset(12f * unit, 13f * unit))
            }
            ZivPlaybackIcon.REPEAT -> {
                line(4f, 10f, 4f, 6f); line(4f, 6f, 19f, 6f); line(16f, 3f, 19f, 6f); line(19f, 6f, 16f, 9f)
                line(20f, 14f, 20f, 18f); line(20f, 18f, 5f, 18f); line(8f, 15f, 5f, 18f); line(5f, 18f, 8f, 21f)
            }
            ZivPlaybackIcon.VOLUME -> {
                drawPath(Path().apply { moveTo(3f * unit, 9f * unit); lineTo(7f * unit, 9f * unit); lineTo(12f * unit, 5f * unit); lineTo(12f * unit, 19f * unit); lineTo(7f * unit, 15f * unit); lineTo(3f * unit, 15f * unit); close() }, ink)
                drawArc(ink, -60f, 120f, false, Offset(11f * unit, 5f * unit), Size(10f * unit, 14f * unit), style = Stroke(1.8f * unit, cap = StrokeCap.Round))
            }
        }
        }
}

@Composable
fun ZivPlaybackText(text: String, modifier: Modifier = Modifier, secondary: Boolean = false, title: Boolean = false,
    compact: Boolean = false, maxLines: Int = 2, textAlign: TextAlign? = null) {
    BasicText(text, modifier, style = TextStyle(
        color = if (secondary) Color(0xFFB6B9C2) else Color.White,
        fontSize = if (title) { if (compact) 16.sp else 18.sp } else { if (compact) 12.sp else 14.sp },
        fontWeight = if (title) FontWeight.SemiBold else FontWeight.Normal,
        textAlign = textAlign ?: TextAlign.Unspecified,
        shadow = Shadow(Color.Black.copy(alpha = 0.4f), Offset(0f, 1f), 2f),
        fontFeatureSettings = "tnum",
    ), maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

@Composable
fun ZivPlaybackTextButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Box(modifier.clip(CircleShape).clickable(enabled = enabled, role = Role.Button, onClick = onClick)
        .padding(horizontal = 16.dp, vertical = 14.dp), contentAlignment = Alignment.Center) {
        ZivPlaybackText(text, secondary = !enabled)
    }
}
