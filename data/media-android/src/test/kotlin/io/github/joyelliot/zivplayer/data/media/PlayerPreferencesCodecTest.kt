// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.mutablePreferencesOf
import androidx.datastore.preferences.core.stringPreferencesKey
import io.github.joyelliot.zivplayer.core.model.*
import org.junit.Assert.*
import org.junit.Test

class PlayerPreferencesCodecTest {
    @Test fun wrongStoredValueTypesRecoverToDefaults() {
        val stored = mutablePreferencesOf(stringPreferencesKey("speed") to "fast",
            intPreferencesKey("shaders") to 42, stringPreferencesKey("resume") to "true")
        assertEquals(PlayerPreferences(), PlayerPreferencesCodec.decode(stored))
        PlayerPreferencesCodec.encode(PlayerPreferences(), stored)
        assertEquals(PlayerPreferences(), PlayerPreferencesCodec.decode(stored))
    }
    @Test fun invalidStoredOptionsRecoverWithoutDiscardingUnrelatedPreferences() {
        val stored = mutablePreferencesOf(
            stringPreferencesKey("appearance") to "DARK",
            stringPreferencesKey("decoder") to "removed-mode",
            intPreferencesKey("speed") to 0,
            intPreferencesKey("subtitle-scale") to Int.MAX_VALUE,
            stringPreferencesKey("subtitle-font") to "../font.ttf",
            stringPreferencesKey("subtitle-font-family") to "name",
            stringPreferencesKey("shaders") to "broken,${"a".repeat(64)},${"a".repeat(64)}",
        )
        val actual = PlayerPreferencesCodec.decode(stored)
        assertEquals(AppearanceMode.DARK, actual.appearance)
        assertEquals(DecoderMode.AUTOMATIC, actual.options.decoder)
        assertEquals(1000, actual.defaultPlaybackRate.value)
        assertEquals(100, actual.options.subtitleScalePercent)
        assertNull(actual.options.subtitleFont)
        assertNull(actual.options.subtitleFontFamily)
        assertEquals(listOf(PlaybackResourceId("a".repeat(64))), actual.options.shaders.ids)
    }

    @Test fun importSelectionOrderAndFontRemovalSurvivePersistence() {
        val font = PlaybackResourceId("1".repeat(64))
        val a = PlaybackResourceId("a".repeat(64))
        val b = PlaybackResourceId("b".repeat(64))
        val stored = mutablePreferencesOf()
        val value = PlayerPreferences(resumePlayback = false, backgroundPlayback = false,
            defaultPlaybackRate = PlaybackRatePermille(1250), options = PlaybackOptions(
                subtitleFont = font, subtitleFontFamily = "测试字体", shaders = ShaderChain(listOf(b, a)),
                audioDelayMs = -150, subtitleDelayMs = 1200, cacheSizeMiB = 256))
        PlayerPreferencesCodec.encode(value, stored)
        assertEquals(value, PlayerPreferencesCodec.decode(stored))
        val removed = value.copy(options = value.options.copy(subtitleFont = null, subtitleFontFamily = null,
            shaders = ShaderChain()))
        PlayerPreferencesCodec.encode(removed, stored)
        assertEquals(removed, PlayerPreferencesCodec.decode(stored))
    }
}
