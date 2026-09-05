// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.playback

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat

internal enum class PlaybackFocusChange { GAIN, LOSS, TRANSIENT_LOSS, DUCK, NOISY }
internal enum class PlaybackFocusAction { NONE, PAUSE, PAUSE_AND_ABANDON, RESUME }

/** User intent survives only a temporary interruption of an accepted play request. */
internal class PlaybackAudioFocusPolicy {
    private var wantsPlayback = false
    private var hasFocus = false
    private var resumeOnGain = false

    fun requestPlay(requestFocus: () -> Boolean): Boolean {
        wantsPlayback = true
        resumeOnGain = false
        hasFocus = hasFocus || requestFocus()
        if (!hasFocus) wantsPlayback = false
        return hasFocus
    }

    fun cancelPlaybackIntent() {
        wantsPlayback = false
        hasFocus = false
        resumeOnGain = false
    }

    fun onChange(change: PlaybackFocusChange): PlaybackFocusAction = when (change) {
        PlaybackFocusChange.GAIN -> {
            val resume = wantsPlayback && resumeOnGain
            hasFocus = wantsPlayback
            resumeOnGain = false
            if (resume) PlaybackFocusAction.RESUME else PlaybackFocusAction.NONE
        }
        PlaybackFocusChange.TRANSIENT_LOSS, PlaybackFocusChange.DUCK -> {
            hasFocus = false
            resumeOnGain = wantsPlayback
            if (wantsPlayback) PlaybackFocusAction.PAUSE else PlaybackFocusAction.NONE
        }
        PlaybackFocusChange.LOSS, PlaybackFocusChange.NOISY -> {
            cancelPlaybackIntent()
            PlaybackFocusAction.PAUSE_AND_ABANDON
        }
    }
}

/** Main-looper owned. A fresh listener token fences callbacks from abandoned requests. */
internal class AndroidPlaybackAudioFocus(
    context: Context,
    looper: Looper,
    private val onChange: (Long, PlaybackFocusChange) -> Unit,
) {
    private val context = context.applicationContext
    private val manager = context.getSystemService(AudioManager::class.java)
    private val handler = Handler(looper)
    private var generation = 0L
    private var request: AudioFocusRequest? = null
    private var noisyReceiver: BroadcastReceiver? = null

    fun isCurrent(token: Long): Boolean = token == generation && request != null

    fun request(): Boolean {
        abandon()
        val token = ++generation
        val next = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE)
                    .build(),
            )
            .setAcceptsDelayedFocusGain(false)
            .setWillPauseWhenDucked(true)
            .setOnAudioFocusChangeListener({ change ->
                val mapped = when (change) {
                    AudioManager.AUDIOFOCUS_GAIN -> PlaybackFocusChange.GAIN
                    AudioManager.AUDIOFOCUS_LOSS -> PlaybackFocusChange.LOSS
                    AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> PlaybackFocusChange.TRANSIENT_LOSS
                    AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> PlaybackFocusChange.DUCK
                    else -> null
                }
                if (mapped != null && isCurrent(token)) onChange(token, mapped)
            }, handler)
            .build()
        request = next
        if (manager.requestAudioFocus(next) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) return true
        abandon()
        return false
    }

    fun setNoisyEnabled(enabled: Boolean) {
        if (enabled == (noisyReceiver != null)) return
        if (enabled) {
            val token = generation
            val receiver = object : BroadcastReceiver() {
                override fun onReceive(context: Context, intent: Intent) {
                    if (noisyReceiver === this && isCurrent(token) &&
                        intent.action == AudioManager.ACTION_AUDIO_BECOMING_NOISY) {
                        onChange(token, PlaybackFocusChange.NOISY)
                    }
                }
            }
            ContextCompat.registerReceiver(
                context, receiver, IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY),
                null, handler, ContextCompat.RECEIVER_NOT_EXPORTED,
            )
            noisyReceiver = receiver
        } else {
            val receiver = noisyReceiver
            noisyReceiver = null
            receiver?.let(context::unregisterReceiver)
        }
    }

    fun abandon() {
        ++generation
        val previous = request
        request = null
        setNoisyEnabled(false)
        if (previous != null) manager.abandonAudioFocusRequest(previous)
    }
}
