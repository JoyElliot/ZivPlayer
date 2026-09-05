// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.joyelliot.zivplayer.core.media.PlaybackResource
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class SettingsViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as ZivPlayerApplication
    val preferences = mutableStateOf(PlayerPreferences())
    val resources = mutableStateOf<List<PlaybackResource>>(emptyList())
    val message = mutableStateOf<String?>(null)
    val busy = mutableStateOf(false)
    val loaded = mutableStateOf(false)
    private var pendingUpdates = 0

    init {
        viewModelScope.launch {
            app.playerPreferences.preferences.catch { message.value = "设置读取失败，请重新打开应用后重试" }
                .collect { if (pendingUpdates == 0) preferences.value = it; loaded.value = true }
        }
        viewModelScope.launch {
            app.mediaRepositories.resources.resources.catch { message.value = "导入资源读取失败" }
                .collect { resources.value = it }
        }
    }

    fun update(transform: (PlayerPreferences) -> PlayerPreferences) {
        if (!loaded.value) return
        // Lifecycle callbacks must see an explicit background/PiP toggle immediately.
        preferences.value = transform(preferences.value)
        pendingUpdates++
        operation {
            try { app.playerPreferences.update(transform) }
            finally {
                pendingUpdates--
                if (pendingUpdates == 0) preferences.value = app.playerPreferences.preferences.first()
            }
        }
    }

    fun importResource(uri: Uri, kind: PlaybackResourceKind) {
        if (busy.value) return
        busy.value = true
        viewModelScope.launch {
            try {
                app.awaitResourceMaintenance()
                val resource = app.mediaRepositories.resources.import(uri, kind)
                message.value = "已导入：${resource.title}"
            } catch (failure: CancellationException) { throw failure
            } catch (failure: Exception) { message.value = failure.message ?: "导入失败"
            } finally { busy.value = false }
        }
    }

    fun deleteResource(resource: PlaybackResource) = operation {
        app.deletePlaybackResource(resource.id)
        message.value = "已删除应用内的导入副本"
    }

    fun clearMessage() { message.value = null }

    private fun operation(block: suspend () -> Unit) = viewModelScope.launch {
        try { block() } catch (failure: CancellationException) { throw failure
        } catch (failure: Exception) { message.value = failure.message ?: "设置保存失败" }
    }
}
