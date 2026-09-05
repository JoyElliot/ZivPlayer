// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.Context
import androidx.datastore.core.handlers.ReplaceFileCorruptionHandler
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStoreFile
import io.github.joyelliot.zivplayer.core.media.PlayerPreferencesRepository
import io.github.joyelliot.zivplayer.core.model.*
import kotlinx.coroutines.flow.map

/** The application owns exactly one instance. Read/write failures reach the caller. */
class DataStorePlayerPreferences(context: Context) : PlayerPreferencesRepository {
    private val store = PreferenceDataStoreFactory.create(
        corruptionHandler = ReplaceFileCorruptionHandler { emptyPreferences() },
        produceFile = { context.applicationContext.preferencesDataStoreFile("player-settings") },
    )
    override val preferences = store.data.map(PlayerPreferencesCodec::decode)

    override suspend fun update(transform: (PlayerPreferences) -> PlayerPreferences) {
        store.edit { stored -> PlayerPreferencesCodec.encode(transform(PlayerPreferencesCodec.decode(stored)), stored) }
    }
}

internal object PlayerPreferencesCodec {
    fun decode(p: Preferences): PlayerPreferences {
        val values = p.asMap()
        fun boolean(key: String, default: Boolean) = values[booleanPreferencesKey(key)] as? Boolean ?: default
        fun integer(key: String, default: Int, range: IntRange) = (values[intPreferencesKey(key)] as? Int)?.takeIf { it in range } ?: default
        val font = string(p, "subtitle-font")?.let { runCatching { PlaybackResourceId(it) }.getOrNull() }
        val family = string(p, "subtitle-font-family")?.takeIf {
            it.isNotBlank() && it.length <= 160 && it.none(Char::isISOControl)
        }
        val shaders = string(p, "shaders").orEmpty().split(',').mapNotNull {
            runCatching { PlaybackResourceId(it) }.getOrNull()
        }.distinct().take(8)
        return PlayerPreferences(
            appearance = enum(p, "appearance", AppearanceMode.SYSTEM),
            resumePlayback = boolean("resume", true),
            backgroundPlayback = boolean("background", true),
            keepScreenOn = boolean("screen-on", true),
            autoPictureInPicture = boolean("auto-pip", false),
            rememberTrackSelection = boolean("remember-tracks", true),
            defaultPlaybackRate = PlaybackRatePermille(integer("speed", 1000, 250..4000)),
            seekStepSeconds = integer("seek-step", 10, 5..60),
            options = PlaybackOptions(
                decoder = enum(p, "decoder", DecoderMode.AUTOMATIC),
                quality = enum(p, "quality", RenderQuality.BALANCED),
                videoFit = enum(p, "video-fit", VideoFit.FIT),
                deinterlace = enum(p, "deinterlace", DeinterlaceMode.OFF),
                toneMapping = enum(p, "tone-mapping", ToneMappingMode.AUTOMATIC),
                audioDelayMs = integer("audio-delay", 0, -10000..10000),
                subtitleDelayMs = integer("subtitle-delay", 0, -60000..60000),
                subtitleScalePercent = integer("subtitle-scale", 100, 50..300),
                subtitlePositionPercent = integer("subtitle-position", 100, 0..100),
                subtitleBorderSize = integer("subtitle-border", 3, 0..6),
                subtitleColor = enum(p, "subtitle-color", SubtitleColor.WHITE),
                overrideStyledSubtitles = boolean("subtitle-override", false),
                subtitleFont = font?.takeIf { family != null },
                subtitleFontFamily = family?.takeIf { font != null },
                shaders = ShaderChain(shaders),
                cacheSizeMiB = integer("cache-mib", 128, 16..512),
            ),
        )
    }

    private inline fun <reified T : Enum<T>> enum(p: Preferences, key: String, default: T): T =
        enumValues<T>().find { it.name == string(p, key) } ?: default

    private fun string(p: Preferences, key: String): String? = p.asMap()[stringPreferencesKey(key)] as? String

    fun encode(value: PlayerPreferences, p: androidx.datastore.preferences.core.MutablePreferences) {
        fun boolean(key: String, v: Boolean) { p[booleanPreferencesKey(key)] = v }
        fun integer(key: String, v: Int) { p[intPreferencesKey(key)] = v }
        fun string(key: String, v: String?) {
            if (v == null) p.remove(stringPreferencesKey(key)) else p[stringPreferencesKey(key)] = v
        }
        string("appearance", value.appearance.name)
        boolean("resume", value.resumePlayback)
        boolean("background", value.backgroundPlayback)
        boolean("screen-on", value.keepScreenOn)
        boolean("auto-pip", value.autoPictureInPicture)
        boolean("remember-tracks", value.rememberTrackSelection)
        integer("speed", value.defaultPlaybackRate.value)
        integer("seek-step", value.seekStepSeconds)
        with(value.options) {
            string("decoder", decoder.name); string("quality", quality.name)
            string("video-fit", videoFit.name); string("deinterlace", deinterlace.name)
            string("tone-mapping", toneMapping.name)
            integer("audio-delay", audioDelayMs); integer("subtitle-delay", subtitleDelayMs)
            integer("subtitle-scale", subtitleScalePercent); integer("subtitle-position", subtitlePositionPercent)
            integer("subtitle-border", subtitleBorderSize); string("subtitle-color", subtitleColor.name)
            boolean("subtitle-override", overrideStyledSubtitles)
            string("subtitle-font", subtitleFont?.value); string("subtitle-font-family", subtitleFontFamily)
            string("shaders", shaders.ids.joinToString(",") { it.value })
            integer("cache-mib", cacheSizeMiB)
        }
    }
}
