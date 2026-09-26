// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.designsystem

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.WindowInsetsController
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalView
import top.yukonga.miuix.kmp.theme.ColorSchemeMode
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.theme.ThemeController
import top.yukonga.miuix.kmp.theme.ThemeColorSpec
import top.yukonga.miuix.kmp.theme.ThemePaletteStyle

enum class ZivAppearance { SYSTEM, LIGHT, DARK }

@Composable
fun ZivTheme(appearance: ZivAppearance = ZivAppearance.SYSTEM, content: @Composable () -> Unit) {
    val controller = remember(appearance) { ThemeController(when (appearance) {
        ZivAppearance.SYSTEM -> ColorSchemeMode.MonetSystem
        ZivAppearance.LIGHT -> ColorSchemeMode.MonetLight
        ZivAppearance.DARK -> ColorSchemeMode.MonetDark
    }, keyColor = Color(0xFF3482FF), colorSpec = ThemeColorSpec.Spec2021,
        paletteStyle = ThemePaletteStyle.Content) }
    MiuixTheme(controller = controller) {
        val view = LocalView.current
        val lightBackground = MiuixTheme.colorScheme.background.luminance() > 0.5f
        if (!view.isInEditMode) SideEffect {
            // App-selected appearance can differ from the device's system appearance.
            val mask = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
            view.context.findActivity()?.window?.insetsController
                ?.setSystemBarsAppearance(if (lightBackground) mask else 0, mask)
        }
        content()
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

@Composable
fun ZivPreviewTheme(content: @Composable () -> Unit) {
    ZivTheme(appearance = ZivAppearance.LIGHT, content = content)
}
