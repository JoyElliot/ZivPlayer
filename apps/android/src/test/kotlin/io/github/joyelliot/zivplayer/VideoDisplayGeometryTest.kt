// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Test

class VideoDisplayGeometryTest {
    @Test fun quarterTurnMetadataProducesPortraitDisplayGeometry() {
        assertEquals(9f / 16f, video(1920, 1080, rotation = 90).displayAspectRatio(), 0.0001f)
        assertEquals(9f / 16f, video(1920, 1080, rotation = 270).displayAspectRatio(), 0.0001f)
    }

    @Test fun nonSquarePixelsUseDisplayRatioBeforeRotation() {
        assertEquals(16f / 9f, video(720, 576, pixelRatio = 64f / 45f).displayAspectRatio(), 0.0001f)
        assertEquals(9f / 16f, video(720, 576, pixelRatio = 64f / 45f, rotation = 90).displayAspectRatio(), 0.0001f)
    }

    @Test fun unavailableGeometryHasAFiniteFallback() {
        assertEquals(16f / 9f, Format.Builder().build().displayAspectRatio(), 0f)
        assertEquals(16f / 9f, video(0, 0).displayAspectRatio(), 0f)
    }

    private fun video(width: Int, height: Int, pixelRatio: Float = 1f, rotation: Int = 0) =
        Format.Builder().setSampleMimeType(MimeTypes.VIDEO_H264).setWidth(width).setHeight(height)
            .setPixelWidthHeightRatio(pixelRatio).setRotationDegrees(rotation).build()
}
