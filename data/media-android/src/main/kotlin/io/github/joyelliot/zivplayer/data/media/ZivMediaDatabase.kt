// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [RecentMediaEntity::class, LibraryFolderEntity::class, LibraryMediaEntity::class,
        TrackChoiceEntity::class, ExternalSubtitleEntity::class, PlaybackResourceEntity::class],
    version = 3,
    exportSchema = true,
)
internal abstract class ZivMediaDatabase : RoomDatabase() {
    abstract fun recentMediaDao(): RecentMediaDao
    abstract fun libraryDao(): LibraryDao
    abstract fun playbackPreferencesDao(): PlaybackPreferencesDao
    abstract fun playbackResourcesDao(): PlaybackResourcesDao
}
