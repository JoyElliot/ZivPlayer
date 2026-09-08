// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.TrackGroup
import androidx.media3.common.Tracks
import org.junit.Assert.assertEquals
import org.junit.Test

class VideoDisplayGeometryDeviceTest {
    @Test fun selectedVideoWinsOverAnUnselectedAlternate() {
        fun video(width: Int, height: Int, selected: Boolean) = Tracks.Group(
            TrackGroup(Format.Builder().setSampleMimeType(MimeTypes.VIDEO_H264).setWidth(width).setHeight(height).build()),
            false, intArrayOf(C.FORMAT_HANDLED), booleanArrayOf(selected),
        )
        assertEquals(9f / 16f, Tracks(listOf(video(1920, 1080, false), video(1080, 1920, true)))
            .videoDisplayAspectRatio(), 0.0001f)
        assertEquals(16f / 9f, Tracks.EMPTY.videoDisplayAspectRatio(), 0f)
    }
}
