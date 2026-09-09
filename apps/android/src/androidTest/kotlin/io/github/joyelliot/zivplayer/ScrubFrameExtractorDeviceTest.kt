// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.graphics.Color
import android.net.Uri
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import kotlin.math.abs

class ScrubFrameExtractorDeviceTest {
    @get:Rule val foreground = DeviceForegroundRule()

    @Test fun coldStartCancelledDecodeReverseAndLastFrameMatchPixels() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        fun checkPixels(frame: ScrubFrameExtractor.Frame) {
            val bitmap = frame.bitmap
            fun white(x: Float): Boolean {
                val value = bitmap.getPixel((x / 1280 * bitmap.width).toInt(), bitmap.height / 2)
                return Color.red(value) > 170 && Color.green(value) > 170 && Color.blue(value) > 170
            }
            check(white(1184f)) { "Missing frame barcode" }
            var code = 0
            for (bit in 0..11) if (white(92f + 88 * bit)) code = code or (1 shl bit)
            check(abs(code * 1_000_000L / 24 - frame.positionUs) < 42_000) {
                "Stale surface pixels: code=$code, PTS=${frame.positionUs}"
            }
        }
        ScrubFrameExtractor(context, Uri.parse("content://io.github.joyelliot.zivplayer.test.fixtures/seek-preview.mp4")).use { decoder ->
            val first = checkNotNull(decoder.frameAt(20_350_000))
            check(abs(first.positionUs - 20_350_000) <= 150_000)
            checkPixels(first)
            val forward = checkNotNull(decoder.frameAt(21_350_000))
            checkPixels(forward)
            val reverse = checkNotNull(decoder.cached(first.positionUs))
            checkPixels(reverse)
            var cancelledChecks = 0
            decoder.frameAt(45_500_000, cancelled = { ++cancelledChecks > 3 })
            val afterCancel = checkNotNull(decoder.frameAt(10_350_000))
            check(abs(afterCancel.positionUs - 10_350_000) <= 150_000)
            checkPixels(afterCancel)
            val tail = checkNotNull(decoder.frameAt(89_999_000))
            check(tail.positionUs in 89_900_000..89_999_999) { "Missing real EOF frame: ${tail.positionUs}" }
            checkPixels(tail)
        }
    }
}
