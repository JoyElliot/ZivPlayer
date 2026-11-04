// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

/** 只有稳定且与实际窗口对齐的端点才能交给独立 Surface 层。 */
internal fun preferDirectVideoOutput(
    pictureInPicture: Boolean,
    pictureInPictureTransition: Boolean,
    playerOpen: Boolean,
    fullyExpanded: Boolean,
    dragging: Boolean,
    rotationAtTarget: Boolean,
    alignedWithWindow: Boolean,
): Boolean = !pictureInPictureTransition && (pictureInPicture ||
    playerOpen && fullyExpanded && !dragging && rotationAtTarget && alignedWithWindow)
