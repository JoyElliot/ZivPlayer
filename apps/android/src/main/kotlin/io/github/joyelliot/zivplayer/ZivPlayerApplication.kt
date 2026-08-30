// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.data.media.MediaDocumentRegistrar
import io.github.joyelliot.zivplayer.data.media.RoomRecentMediaRepository

class ZivPlayerApplication : Application() {
    val recentMediaRepository: RecentMediaRepository by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        RoomRecentMediaRepository.create(this)
    }

    val mediaDocumentRegistrar: MediaDocumentRegistrar by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        MediaDocumentRegistrar.create(this, recentMediaRepository)
    }
}
