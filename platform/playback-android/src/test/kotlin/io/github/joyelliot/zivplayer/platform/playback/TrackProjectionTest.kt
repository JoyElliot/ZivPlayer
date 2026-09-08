// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import io.github.joyelliot.zivplayer.core.model.TrackDescriptor
import io.github.joyelliot.zivplayer.core.model.TrackId
import io.github.joyelliot.zivplayer.core.model.TrackKind
import org.junit.Assert.assertEquals
import org.junit.Test

class TrackProjectionTest {
    @Test fun media3ReceivesVideoRotationAndPixelAspect() {
        val track = TrackDescriptor(TrackId("video:1"), TrackKind.VIDEO, width = 720, height = 576,
            pixelWidthHeightRatio = 64f / 45f, rotationDegrees = 90)
        val format = track.toMedia3Format()
        assertEquals(720, format.width)
        assertEquals(576, format.height)
        assertEquals(64f / 45f, format.pixelWidthHeightRatio, 0f)
        assertEquals(90, format.rotationDegrees)
    }
}
