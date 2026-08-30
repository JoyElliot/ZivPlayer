// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.res.stringResource
import io.github.joyelliot.zivplayer.designsystem.ZivPreviewTheme
import io.github.joyelliot.zivplayer.designsystem.ZivPrimaryButton
import io.github.joyelliot.zivplayer.designsystem.ZivScreen
import io.github.joyelliot.zivplayer.designsystem.ZivText

@Composable
fun PlayerHomeScreen(onOpenMedia: () -> Unit = {}) {
    ZivScreen(title = stringResource(R.string.player_title)) {
        ZivText(text = stringResource(R.string.player_bootstrap_description))
        ZivPrimaryButton(
            text = stringResource(R.string.player_open_media),
            onClick = onOpenMedia,
        )
    }
}

@Preview(showBackground = true)
@Composable
private fun PlayerHomeScreenPreview() {
    ZivPreviewTheme {
        PlayerHomeScreen()
    }
}
