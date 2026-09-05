// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import top.yukonga.miuix.kmp.theme.ColorSchemeMode
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.theme.ThemeController
import top.yukonga.miuix.kmp.theme.lightColorScheme

enum class ZivAppearance { SYSTEM, LIGHT, DARK }

@Composable
fun ZivTheme(appearance: ZivAppearance = ZivAppearance.SYSTEM, content: @Composable () -> Unit) {
    val controller = remember(appearance) { ThemeController(when (appearance) {
        ZivAppearance.SYSTEM -> ColorSchemeMode.System
        ZivAppearance.LIGHT -> ColorSchemeMode.Light
        ZivAppearance.DARK -> ColorSchemeMode.Dark
    }) }
    MiuixTheme(controller = controller, content = content)
}

@Composable
fun ZivPreviewTheme(content: @Composable () -> Unit) {
    MiuixTheme(colors = lightColorScheme(), content = content)
}
