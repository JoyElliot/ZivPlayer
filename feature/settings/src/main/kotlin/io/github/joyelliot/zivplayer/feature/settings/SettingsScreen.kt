// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.settings

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import io.github.joyelliot.zivplayer.core.media.PlaybackResource
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.designsystem.*
import kotlin.math.roundToInt

/** Routes belong to settings; the reusable page stack only stores their stable names. */
enum class SettingsPage(val title: Int, val icon: ZivPlaybackIcon) {
    GENERAL(R.string.settings_general, ZivPlaybackIcon.PLAY),
    VIDEO(R.string.settings_video, ZivPlaybackIcon.FIT),
    AUDIO(R.string.settings_audio, ZivPlaybackIcon.AUDIO),
    SUBTITLES(R.string.settings_subtitles, ZivPlaybackIcon.SUBTITLES),
    FONTS(R.string.settings_fonts, ZivPlaybackIcon.SUBTITLES),
    SHADERS(R.string.settings_shaders, ZivPlaybackIcon.SETTINGS),
    RESOURCES(R.string.settings_resources, ZivPlaybackIcon.FOLDER),
    DIAGNOSTICS(R.string.settings_diagnostics, ZivPlaybackIcon.LIST),
}

@Composable
fun SettingsScreen(
    message: String?,
    onOpenPage: (SettingsPage) -> Unit,
    onReset: () -> Unit,
    bottomPadding: Dp = 0.dp,
) {
    ZivScreen(stringResource(R.string.settings_title), bottomPadding = bottomPadding) {
        message?.let { ZivStatusText(it) }
        listOf(
            SettingsPage.GENERAL to R.string.settings_general_summary,
            SettingsPage.VIDEO to R.string.settings_video_summary,
            SettingsPage.AUDIO to R.string.settings_audio_summary,
            SettingsPage.SUBTITLES to R.string.settings_subtitles_summary,
            SettingsPage.SHADERS to R.string.settings_shaders_summary,
            SettingsPage.RESOURCES to R.string.settings_resources_summary,
            SettingsPage.DIAGNOSTICS to R.string.settings_diagnostics_summary,
        ).forEach { (page, summary) -> SettingsNavigationRow(page, stringResource(summary), onOpenPage) }
        ZivPrimaryButton(stringResource(R.string.settings_reset), onReset, Modifier.fillMaxWidth())
    }
}

@Composable
fun SettingsDetailScreen(
    page: SettingsPage,
    value: PlayerPreferences, resources: List<PlaybackResource>, message: String?, busy: Boolean,
    onUpdate: ((PlayerPreferences) -> PlayerPreferences) -> Unit,
    onImport: (PlaybackResourceKind) -> Unit, onDelete: (PlaybackResource) -> Unit,
    onBack: () -> Unit, onOpenPage: (SettingsPage) -> Unit,
) {
    val options = value.options
    ZivScreen(stringResource(page.title), leading = {
        ZivLibraryIconButton(ZivPlaybackIcon.BACK, stringResource(R.string.settings_back), onBack)
    }) {
        message?.let { ZivStatusText(it) }
        when (page) {
            SettingsPage.GENERAL -> {
                Choice(stringResource(R.string.settings_appearance), stringResource(when (value.appearance) {
                    AppearanceMode.SYSTEM -> R.string.option_system
                    AppearanceMode.LIGHT -> R.string.option_light
                    AppearanceMode.DARK -> R.string.option_dark
                })) { onUpdate { it.copy(appearance = next(it.appearance)) } }
                ZivToggleRow(stringResource(R.string.settings_resume), value.resumePlayback, { selected -> onUpdate { it.copy(resumePlayback = selected) } })
                ZivToggleRow(stringResource(R.string.settings_background), value.backgroundPlayback, { selected -> onUpdate { it.copy(backgroundPlayback = selected) } })
                ZivToggleRow(stringResource(R.string.settings_screen_on), value.keepScreenOn, { selected -> onUpdate { it.copy(keepScreenOn = selected) } })
                ZivToggleRow(stringResource(R.string.settings_auto_pip), value.autoPictureInPicture, { selected -> onUpdate { it.copy(autoPictureInPicture = selected) } })
                ZivToggleRow(stringResource(R.string.settings_tracks), value.rememberTrackSelection, { selected -> onUpdate { it.copy(rememberTrackSelection = selected) } })
                Choice(stringResource(R.string.settings_speed), "${value.defaultPlaybackRate.value / 1000f}×") {
                    onUpdate { it.copy(defaultPlaybackRate = PlaybackRatePermille(nextValue(it.defaultPlaybackRate.value, listOf(500, 750, 1000, 1250, 1500, 2000, 3000, 4000)))) }
                }
                Choice(stringResource(R.string.settings_seek), "${value.seekStepSeconds} s") {
                    onUpdate { it.copy(seekStepSeconds = nextValue(it.seekStepSeconds, listOf(5, 10, 15, 30, 60))) }
                }

            }
            SettingsPage.VIDEO -> {
                Choice(stringResource(R.string.settings_decoder), stringResource(if (options.decoder == DecoderMode.AUTOMATIC) R.string.option_auto else R.string.option_software)) {
                    onUpdate { it.copy(options = it.options.copy(decoder = next(it.options.decoder))) }
                }
                Choice(stringResource(R.string.settings_quality), stringResource(when (options.quality) {
                    RenderQuality.EFFICIENT -> R.string.option_efficient
                    RenderQuality.BALANCED -> R.string.option_balanced
                    RenderQuality.HIGH -> R.string.option_high
                })) { onUpdate { it.copy(options = it.options.copy(quality = next(it.options.quality))) } }
                ZivText(stringResource(R.string.settings_quality_hint))
                Choice(stringResource(R.string.settings_fit), stringResource(when (options.videoFit) {
                    VideoFit.FIT -> R.string.option_fit
                    VideoFit.FILL -> R.string.option_fill
                    VideoFit.STRETCH -> R.string.option_stretch
                })) { onUpdate { it.copy(options = it.options.copy(videoFit = next(it.options.videoFit))) } }
                Choice(stringResource(R.string.settings_deinterlace), stringResource(when (options.deinterlace) {
                    DeinterlaceMode.OFF -> R.string.option_off
                    DeinterlaceMode.AUTOMATIC -> R.string.option_auto
                    DeinterlaceMode.ON -> R.string.option_on
                })) { onUpdate { it.copy(options = it.options.copy(deinterlace = next(it.options.deinterlace))) } }
                Choice(stringResource(R.string.settings_tone), when (options.toneMapping) {
                    ToneMappingMode.AUTOMATIC -> stringResource(R.string.option_auto)
                    ToneMappingMode.BT2390 -> "BT.2390"
                    ToneMappingMode.HABLE -> "Hable"
                    ToneMappingMode.CLIP -> "Clip"
                }) { onUpdate { it.copy(options = it.options.copy(toneMapping = next(it.options.toneMapping))) } }
                ZivText(stringResource(R.string.settings_tone_hint))
                Choice(stringResource(R.string.settings_cache), "${options.cacheSizeMiB} MiB") {
                    onUpdate { it.copy(options = it.options.copy(cacheSizeMiB = nextValue(it.options.cacheSizeMiB, listOf(16, 32, 64, 128, 256, 512)))) }
                }

            }
            SettingsPage.AUDIO -> {
                IntegerSlider(stringResource(R.string.settings_audio_delay), options.audioDelayMs, -10000..10000, 50, "ms") { changed ->
                    onUpdate { it.copy(options = it.options.copy(audioDelayMs = changed)) }
                }
                ZivText(stringResource(R.string.settings_audio_delay_hint))

            }
            SettingsPage.SUBTITLES -> {
                IntegerSlider(stringResource(R.string.settings_sub_delay), options.subtitleDelayMs, -60000..60000, 100, "ms") { changed ->
                    onUpdate { it.copy(options = it.options.copy(subtitleDelayMs = changed)) }
                }
                IntegerSlider(stringResource(R.string.settings_sub_scale), options.subtitleScalePercent, 50..300, 5, "%") { changed ->
                    onUpdate { it.copy(options = it.options.copy(subtitleScalePercent = changed)) }
                }
                IntegerSlider(stringResource(R.string.settings_sub_position), options.subtitlePositionPercent, 0..100, 1, "%") { changed ->
                    onUpdate { it.copy(options = it.options.copy(subtitlePositionPercent = changed)) }
                }
                IntegerSlider(stringResource(R.string.settings_sub_border), options.subtitleBorderSize, 0..6, 1, "") { changed ->
                    onUpdate { it.copy(options = it.options.copy(subtitleBorderSize = changed)) }
                }
                Choice(stringResource(R.string.settings_sub_color), stringResource(when (options.subtitleColor) {
                    SubtitleColor.WHITE -> R.string.option_white
                    SubtitleColor.YELLOW -> R.string.option_yellow
                    SubtitleColor.GREEN -> R.string.option_green
                })) { onUpdate { it.copy(options = it.options.copy(subtitleColor = next(it.options.subtitleColor))) } }
                ZivToggleRow(stringResource(R.string.settings_sub_override), options.overrideStyledSubtitles, { selected ->
                    onUpdate { it.copy(options = it.options.copy(overrideStyledSubtitles = selected)) }
                }, detail = stringResource(R.string.settings_sub_override_hint))
                SettingsNavigationRow(SettingsPage.FONTS,
                    stringResource(R.string.settings_font_current, options.subtitleFontFamily ?: stringResource(R.string.option_default)),
                    onOpenPage)

            }
            SettingsPage.SHADERS -> {
                ZivText(stringResource(R.string.settings_shader_hint))
                val ordered = options.shaders.ids.mapNotNull { id -> resources.find { it.id == id } } +
                    resources.filter { it.kind == PlaybackResourceKind.SHADER && it.id !in options.shaders.ids }
                ordered.forEach { shader ->
                    val index = options.shaders.ids.indexOf(shader.id)
                    ZivToggleRow(if (index >= 0) "${index + 1}. ${shader.title}" else shader.title,
                        index >= 0, { enabled -> onUpdate {
                            it.copy(options = it.options.copy(shaders = ShaderChain(if (enabled) it.options.shaders.ids + shader.id else it.options.shaders.ids - shader.id)))
                        } }, enabled = index >= 0 || options.shaders.ids.size < 8)
                    if (index > 0) ZivPrimaryButton(stringResource(R.string.settings_move_up), { onUpdate {
                        val ids = it.options.shaders.ids.toMutableList()
                        val position = ids.indexOf(shader.id)
                        if (position > 0) java.util.Collections.swap(ids, position - 1, position)
                        it.copy(options = it.options.copy(shaders = ShaderChain(ids)))
                    } })
                }
                ZivPrimaryButton(stringResource(R.string.settings_import_shader), { onImport(PlaybackResourceKind.SHADER) }, enabled = !busy)

            }
            SettingsPage.RESOURCES -> {
                ZivText(stringResource(R.string.settings_resource_hint))
                if (resources.isEmpty()) ZivText(stringResource(R.string.settings_resource_empty))
                resources.forEach { resource ->
                    ZivText("${resource.title} · ${resource.sizeBytes / 1024} KiB")
                    ZivPrimaryButton(stringResource(R.string.settings_delete_copy), { onDelete(resource) }, enabled = !busy)
                }

            }
            SettingsPage.FONTS -> {
                ZivText(stringResource(R.string.settings_font_current, options.subtitleFontFamily ?: stringResource(R.string.option_default)))
                ZivPrimaryButton(stringResource(R.string.settings_font_default), {
                    onUpdate { it.copy(options = it.options.copy(subtitleFont = null, subtitleFontFamily = null)) }
                }, enabled = options.subtitleFont != null)
                resources.filter { it.kind == PlaybackResourceKind.FONT }.forEach { font ->
                    ZivPrimaryButton(font.fontFamily ?: font.title, { onUpdate {
                        it.copy(options = it.options.copy(subtitleFont = font.id, subtitleFontFamily = font.fontFamily))
                    } }, enabled = font.id != options.subtitleFont)
                }
                ZivPrimaryButton(stringResource(R.string.settings_import_font), { onImport(PlaybackResourceKind.FONT) }, enabled = !busy)

            }
            SettingsPage.DIAGNOSTICS -> error("Diagnostics uses DiagnosticsScreen")
        }
    }
}

@Composable
private fun SettingsNavigationRow(page: SettingsPage, detail: String, onOpenPage: (SettingsPage) -> Unit) {
    ZivLibraryRow(stringResource(page.title), detail, page.icon, { onOpenPage(page) },
        Modifier.testTag("settings_open_${page.name}"), trailing = {
            ZivLibraryText("›", Modifier.padding(horizontal = 12.dp), secondary = true)
        })
}

@Composable private fun Choice(title: String, selected: String, onNext: () -> Unit) {
    ZivPrimaryButton("$title · $selected", onNext, Modifier.fillMaxWidth())
}

@Composable private fun IntegerSlider(title: String, value: Int, range: IntRange, step: Int, suffix: String, onChanged: (Int) -> Unit) {
    var draft by remember(value) { mutableStateOf(value) }
    ZivText("$title · $draft $suffix")
    ZivSlider(draft.toFloat(), { draft = ((it / step).roundToInt() * step).coerceIn(range) },
        valueRange = range.first.toFloat()..range.last.toFloat(), onValueChangeFinished = { onChanged(draft) })
    if (range.first < 0) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ZivPrimaryButton("−$step", { onChanged((value - step).coerceIn(range)) }, Modifier.weight(1f))
        ZivPrimaryButton(stringResource(R.string.option_reset), { onChanged(0) }, Modifier.weight(1f))
        ZivPrimaryButton("+$step", { onChanged((value + step).coerceIn(range)) }, Modifier.weight(1f))
    }
}

private inline fun <reified T : Enum<T>> next(current: T): T = enumValues<T>().let { it[(current.ordinal + 1) % it.size] }
private fun nextValue(current: Int, values: List<Int>) = values[(values.indexOf(current) + 1) % values.size]
