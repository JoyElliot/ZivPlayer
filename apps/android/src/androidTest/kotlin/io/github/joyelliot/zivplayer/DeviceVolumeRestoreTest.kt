// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.media.AudioManager
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeNotNull
import org.junit.Test

/** Explicit host recovery hook: some OEM shell volume commands rescale the supplied index. */
class DeviceVolumeRestoreTest {
    @Test fun restoreRecordedMediaVolume() {
        val index = InstrumentationRegistry.getArguments().getString("restoreMediaVolume")?.toIntOrNull()
        assumeNotNull(index)
        val audio = InstrumentationRegistry.getInstrumentation().targetContext.getSystemService(AudioManager::class.java)
        val target = checkNotNull(index)
        require(target in audio.getStreamMinVolume(AudioManager.STREAM_MUSIC)..audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC))
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, target, 0)
        assertEquals(target, audio.getStreamVolume(AudioManager.STREAM_MUSIC))
    }
}
