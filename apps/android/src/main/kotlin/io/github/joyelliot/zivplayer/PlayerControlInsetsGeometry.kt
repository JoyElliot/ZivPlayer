// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import kotlin.math.abs
import kotlin.math.roundToInt

internal data class PlayerSafeArea(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    fun turned(quarters: Int): PlayerSafeArea {
        val sides = intArrayOf(left, top, right, bottom)
        val turn = (quarters % 4 + 4) % 4
        return PlayerSafeArea(sides[turn], sides[(turn + 1) % 4], sides[(turn + 2) % 4], sides[(turn + 3) % 4])
    }
}

/** Insets and window axes arrive separately. Keep the entry safe area through a rotation session. */
internal class PlayerControlInsetsLatch {
    private var entry: PlayerSafeArea? = null
    private var rotating = false

    fun resolve(source: () -> PlayerSafeArea, windowTurns: Int, angle: Float, sourceReady: Boolean = true): PlayerSafeArea {
        val startsRotation = abs(angle) > 0.001f || windowTurns != 0
        if (sourceReady && (entry == null || (!rotating && !startsRotation))) {
            entry = source().turned(-windowTurns)
        }
        // A restored horizontal window can be measured before its first insets dispatch.
        // Draw provisionally, but never freeze an unavailable (or previous-window) snapshot.
        val current = entry ?: return source().turned(-windowTurns).turned((angle / 90f).roundToInt())
        // Capture before the system moves its bars/cutout; keep it for the reverse handoff too.
        // A new presentation basis or real logical-window resize creates a new latch.
        rotating = rotating || startsRotation
        return current.turned((angle / 90f).roundToInt())
    }
}
