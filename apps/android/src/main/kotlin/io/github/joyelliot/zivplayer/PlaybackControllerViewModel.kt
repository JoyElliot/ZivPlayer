// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import android.content.ComponentName
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import io.github.joyelliot.zivplayer.platform.playback.PlaybackService

/** Keeps one MediaController across Activity recreation without retaining an Activity context. */
class PlaybackControllerViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val mutableController = mutableStateOf<MediaController?>(null)
    val controller: State<MediaController?> = mutableController

    private val controllerFuture: ListenableFuture<MediaController> = MediaController.Builder(
        application,
        SessionToken(application, ComponentName(application, PlaybackService::class.java)),
    ).buildAsync()
    private var cleared = false

    init {
        controllerFuture.addListener(
            {
                runCatching(controllerFuture::get).onSuccess { connectedController ->
                    if (cleared) {
                        MediaController.releaseFuture(controllerFuture)
                    } else {
                        mutableController.value = connectedController
                    }
                }
            },
            ContextCompat.getMainExecutor(application),
        )
    }

    override fun onCleared() {
        cleared = true
        mutableController.value = null
        MediaController.releaseFuture(controllerFuture)
    }
}
