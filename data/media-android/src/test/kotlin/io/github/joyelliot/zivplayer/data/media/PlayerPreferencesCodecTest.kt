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
            intPreferencesKey("shaders") to 42, stringPreferencesKey("resume") to "true",
            intPreferencesKey("navigation-style") to 1)
        assertEquals(PlayerPreferences(), PlayerPreferencesCodec.decode(stored))
        PlayerPreferencesCodec.encode(PlayerPreferences(), stored)
        assertEquals(PlayerPreferences(), PlayerPreferencesCodec.decode(stored))
    }
    @Test fun invalidStoredOptionsRecoverWithoutDiscardingUnrelatedPreferences() {
        val stored = mutablePreferencesOf(
            stringPreferencesKey("appearance") to "DARK",
            stringPreferencesKey("decoder") to "removed-mode",
            stringPreferencesKey("navigation-style") to "removed-style",
            intPreferencesKey("speed") to 0,
            intPreferencesKey("subtitle-scale") to Int.MAX_VALUE,
            stringPreferencesKey("subtitle-font") to "../font.ttf",
            stringPreferencesKey("subtitle-font-family") to "name",
            stringPreferencesKey("shaders") to "broken,${"a".repeat(64)},${"a".repeat(64)}",
        )
        val actual = PlayerPreferencesCodec.decode(stored)
        assertEquals(AppearanceMode.DARK, actual.appearance)
        assertEquals(NavigationStyle.LIQUID_GLASS, actual.navigationStyle)
        assertEquals(DecoderMode.AUTOMATIC, actual.options.decoder)
        assertEquals(1000, actual.defaultPlaybackRate.value)
        assertEquals(100, actual.options.subtitleScalePercent)
        assertNull(actual.options.subtitleFont)
        assertNull(actual.options.subtitleFontFamily)
        assertEquals(listOf(PlaybackResourceId("a".repeat(64))), actual.options.shaders.ids)
    }

    @Test fun anExistingInstallationDefaultsToGlassWithoutResettingPlaybackChoices() {
        val stored = mutablePreferencesOf(stringPreferencesKey("appearance") to "DARK",
            intPreferencesKey("speed") to 1250, intPreferencesKey("audio-delay") to -150)
        val actual = PlayerPreferencesCodec.decode(stored)
        assertEquals(NavigationStyle.LIQUID_GLASS, actual.navigationStyle)
        assertEquals(AppearanceMode.DARK, actual.appearance)
        assertEquals(1250, actual.defaultPlaybackRate.value)
        assertEquals(-150, actual.options.audioDelayMs)
        assertFalse(stored.contains(stringPreferencesKey("navigation-style")))
    }

    @Test fun bothDockStylesPersistIndependentlyOfPlaybackOptionsAndResetToGlass() {
        val stored = mutablePreferencesOf()
        val original = PlayerPreferences(appearance = AppearanceMode.DARK,
            backgroundPlayback = false, options = PlaybackOptions(videoFit = VideoFit.FILL))
        for (style in listOf(NavigationStyle.SIMPLE, NavigationStyle.LIQUID_GLASS)) {
            val changed = original.copy(navigationStyle = style)
            PlayerPreferencesCodec.encode(changed, stored)
            assertEquals(changed, PlayerPreferencesCodec.decode(stored))
        }
        PlayerPreferencesCodec.encode(original.copy(navigationStyle = NavigationStyle.SIMPLE), stored)
        PlayerPreferencesCodec.encode(PlayerPreferences(), stored)
        assertEquals(NavigationStyle.LIQUID_GLASS, PlayerPreferencesCodec.decode(stored).navigationStyle)
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
