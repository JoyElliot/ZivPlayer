// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [RecentMediaEntity::class],
    version = 1,
    exportSchema = true,
)
internal abstract class ZivMediaDatabase : RoomDatabase() {
    abstract fun recentMediaDao(): RecentMediaDao
}
