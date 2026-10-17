// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** Public SDK only. Poll while UI is started: Android has no public stream-volume listener. */
internal class SystemMediaVolume(context: Context, private val applyGain: (Float) -> Unit,
    private val onChanged: () -> Unit) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val handler = Handler(Looper.getMainLooper())
    private val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build()
    private var minimum = audio.getStreamMinVolume(AudioManager.STREAM_MUSIC)
    private var maximum = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
    private var observedIndex: Int? = null
    private var route: List<Pair<Int, Int>>? = null
    private var routeCheckedAt = Long.MIN_VALUE
    private var deviceType: Int? = null
    private var active = false
    private var gain = 1f
    var level by mutableFloatStateOf(normalized(audio.getStreamVolume(AudioManager.STREAM_MUSIC)))
        private set
    var canAdjust by mutableStateOf(!audio.isVolumeFixed && maximum > minimum)
        private set

    private val callback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>) { refresh(forceRoute = true) }
        override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>) { refresh(forceRoute = true) }
    }
    private val poll = object : Runnable {
        override fun run() { if (active) { refresh(); handler.postDelayed(this, 250L) } }
    }

    fun setActive(value: Boolean) {
        if (active == value) return
        active = value
        if (value) {
            audio.registerAudioDeviceCallback(callback, handler)
            refresh(forceRoute = true)
            handler.post(poll)
        } else {
            audio.unregisterAudioDeviceCallback(callback)
            handler.removeCallbacks(poll)
        }
    }

    fun refresh(forceRoute: Boolean = false, hardwareKey: Boolean = false) {
        val now = SystemClock.uptimeMillis()
        var routeChanged = false
        if (forceRoute || routeCheckedAt == Long.MIN_VALUE || now - routeCheckedAt >= 1_000L) {
            routeCheckedAt = now
            val devices = runCatching { audio.getAudioDevicesForAttributes(attributes) }.getOrDefault(emptyList())
            val nextRoute = devices.map { it.id to it.type }.sortedBy { it.first }
            routeChanged = route != null && route != nextRoute
            route = nextRoute
            // This is the expected route, not proof of an active AudioTrack's routing.
            deviceType = devices.singleOrNull()?.type
            minimum = audio.getStreamMinVolume(AudioManager.STREAM_MUSIC)
            maximum = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
            canAdjust = !audio.isVolumeFixed && maximum > minimum
        }
        val index = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
        if (observedIndex != index || routeChanged || hardwareKey) {
            observedIndex = index
            level = normalized(index)
            setGain(1f)
            onChanged()
        }
    }

    fun changeLevel(value: Float) {
        if (!canAdjust || !value.isFinite()) return
        refresh()
        val step = mediaVolumeStep(value, minimum, maximum) { index ->
            deviceType?.let { type -> runCatching { audio.getStreamVolumeDb(AudioManager.STREAM_MUSIC, index, type) }.getOrNull() }
        }
        val accepted = runCatching { audio.setStreamVolume(AudioManager.STREAM_MUSIC, step.index, 0) }.isSuccess
        val actual = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
        observedIndex = actual
        // Safe-volume limits and Android 17 background restrictions may silently reject the write.
        if (accepted && actual == step.index) {
            level = if (step.gain == 1f) normalized(actual) else value.coerceIn(0f, 1f)
            setGain(step.gain)
        } else {
            level = normalized(actual)
            setGain(1f)
        }
        onChanged()
    }

    fun applyCurrentGain() = applyGain(gain)
    fun close() = setActive(false)
    private fun setGain(value: Float) { if (gain != value) { gain = value; applyGain(value) } }
    private fun normalized(index: Int) = if (maximum <= minimum) 1f
        else ((index - minimum).toFloat() / (maximum - minimum)).coerceIn(0f, 1f)
}
