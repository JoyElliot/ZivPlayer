// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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

    @Test fun nativeBufferPreservesSourceSamplesInsteadOfUpscalingToTheDisplay() {
        assertEquals(1920 to 1080, video(1920, 1080).nativeRenderSize())
        assertEquals(640 to 360, video(640, 360).nativeRenderSize())
        assertEquals(1919 to 1079, video(1919, 1079).nativeRenderSize())
        assertEquals(3840 to 2160, video(3840, 2160).nativeRenderSize())
        assertNull(video(0, 0).nativeRenderSize())
    }

    @Test fun nativeBufferCorrectsPixelAspectAndRotationWithoutLetterboxing() {
        assertEquals(1024 to 576, video(720, 576, pixelRatio = 64f / 45f).nativeRenderSize())
        assertEquals(576 to 1024, video(720, 576, pixelRatio = 64f / 45f, rotation = -90).nativeRenderSize())
        assertEquals(720 to 1152, video(720, 576, pixelRatio = 0.5f).nativeRenderSize())
        assertEquals(1080 to 1920, video(1920, 1080, rotation = 270).nativeRenderSize())
        assertEquals(4096 to 2304, video(7680, 4320).nativeRenderSize())
    }

    private fun video(width: Int, height: Int, pixelRatio: Float = 1f, rotation: Int = 0) =
        Format.Builder().setSampleMimeType(MimeTypes.VIDEO_H264).setWidth(width).setHeight(height)
            .setPixelWidthHeightRatio(pixelRatio).setRotationDegrees(rotation).build()
}
