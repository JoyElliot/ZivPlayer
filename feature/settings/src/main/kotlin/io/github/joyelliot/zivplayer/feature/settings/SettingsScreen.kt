// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.settings

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.joyelliot.zivplayer.core.media.PlaybackResource
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.designsystem.*
import kotlin.math.roundToInt

@Composable
fun SettingsScreen(
    value: PlayerPreferences, resources: List<PlaybackResource>, message: String?, busy: Boolean,
    onUpdate: ((PlayerPreferences) -> PlayerPreferences) -> Unit,
    onImport: (PlaybackResourceKind) -> Unit, onDelete: (PlaybackResource) -> Unit,
    onDiagnostics: () -> Unit,
) {
    val options = value.options
    ZivScreen(stringResource(R.string.settings_title)) {
        message?.let { ZivStatusText(it) }
        Section(stringResource(R.string.settings_general), initiallyExpanded = true) {
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
        Section(stringResource(R.string.settings_video)) {
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
        Section(stringResource(R.string.settings_audio)) {
            IntegerSlider(stringResource(R.string.settings_audio_delay), options.audioDelayMs, -10000..10000, 50, "ms") { changed ->
                onUpdate { it.copy(options = it.options.copy(audioDelayMs = changed)) }
            }
            ZivText(stringResource(R.string.settings_audio_delay_hint))
        }
        Section(stringResource(R.string.settings_subtitles)) {
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
        Section(stringResource(R.string.settings_shaders)) {
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
        Section(stringResource(R.string.settings_resources)) {
            ZivText(stringResource(R.string.settings_resource_hint))
            if (resources.isEmpty()) ZivText(stringResource(R.string.settings_resource_empty))
            resources.forEach { resource ->
                ZivText("${resource.title} · ${resource.sizeBytes / 1024} KiB")
                ZivPrimaryButton(stringResource(R.string.settings_delete_copy), { onDelete(resource) }, enabled = !busy)
            }
        }
        ZivPrimaryButton(stringResource(R.string.settings_diagnostics), onDiagnostics, Modifier.fillMaxWidth())
        ZivPrimaryButton(stringResource(R.string.settings_reset), { onUpdate { PlayerPreferences() } }, Modifier.fillMaxWidth())
    }
}

@Composable private fun Section(title: String, initiallyExpanded: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(initiallyExpanded) }
    ZivCard {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ZivPrimaryButton((if (expanded) "− " else "+ ") + title, { expanded = !expanded }, Modifier.fillMaxWidth())
            if (expanded) content()
        }
    }
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
