// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.playback

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaStyleNotificationHelper

/**
 * targetSdk 35+ requires foreground service status before background audio-focus
 * requests. Media3 will replace this notification with its normal media controls
 * when the Player publishes playback. Both paths use the same channel and ID.
 */
internal class PlaybackForegroundGate(private val service: Service) {
    private var promotionPending = false

    fun begin(session: MediaSession) {
        val manager = service.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                DefaultMediaNotificationProvider.DEFAULT_CHANNEL_ID,
                service.getString(DefaultMediaNotificationProvider.DEFAULT_CHANNEL_NAME_RESOURCE_ID),
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
        val notification = NotificationCompat.Builder(
            service, DefaultMediaNotificationProvider.DEFAULT_CHANNEL_ID,
        )
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(session.player.mediaMetadata.title ?: "ZivPlayer")
            .setContentIntent(session.sessionActivity)
            .setStyle(MediaStyleNotificationHelper.MediaStyle(session))
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .build()
        ServiceCompat.startForeground(
            service, DefaultMediaNotificationProvider.DEFAULT_NOTIFICATION_ID, notification,
            if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK else 0,
        )
        promotionPending = true
    }

    fun onPlaybackPublished() {
        promotionPending = false
    }

    fun cancelPendingPromotion() {
        if (promotionPending) {
            ServiceCompat.stopForeground(service, ServiceCompat.STOP_FOREGROUND_REMOVE)
            promotionPending = false
        }
    }
}
