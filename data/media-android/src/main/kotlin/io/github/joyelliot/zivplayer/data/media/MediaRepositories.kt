// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.Context
import androidx.room.Room
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import io.github.joyelliot.zivplayer.core.model.MediaId
import java.io.Closeable
import java.util.UUID

/** One Room instance shared by history, library, resources and media preferences. */
class MediaRepositories(context: Context, databaseName: String = "zivplayer-media.db") : Closeable {
    val documentOperations = kotlinx.coroutines.sync.Mutex()
    internal val database = Room.databaseBuilder(context.applicationContext,
        ZivMediaDatabase::class.java, databaseName).addMigrations(MIGRATION_1_2).build()
    val recent = RoomRecentMediaRepository(database) { MediaId(UUID.randomUUID().toString()) }
    val library = RoomMediaLibraryRepository(database)
    val playbackPreferences = RoomMediaPlaybackPreferencesRepository(database)
    val resources = ManagedPlaybackResources(context.applicationContext, database)
    override fun close() = database.close()
}

internal val MIGRATION_1_2: Migration = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("""CREATE TABLE IF NOT EXISTS library_folders (
            folder_id TEXT NOT NULL PRIMARY KEY, source_uri TEXT NOT NULL, name TEXT NOT NULL,
            status TEXT NOT NULL, last_scan_epoch_ms INTEGER, media_count INTEGER NOT NULL,
            message TEXT, scan_token TEXT)""")
        db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_library_folders_source_uri ON library_folders(source_uri)")
        db.execSQL("""CREATE TABLE IF NOT EXISTS library_media (
            folder_id TEXT NOT NULL, source_uri TEXT NOT NULL, title TEXT NOT NULL, mime_type TEXT,
            relative_path TEXT NOT NULL, size_bytes INTEGER, modified_epoch_ms INTEGER,
            favorite INTEGER NOT NULL, hidden INTEGER NOT NULL, PRIMARY KEY(folder_id, source_uri),
            FOREIGN KEY(folder_id) REFERENCES library_folders(folder_id) ON UPDATE NO ACTION ON DELETE CASCADE)""")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_library_media_source_uri ON library_media(source_uri)")
        db.execSQL("""CREATE TABLE IF NOT EXISTS media_track_choices (
            media_id TEXT NOT NULL, kind TEXT NOT NULL, track_index INTEGER, language TEXT, label TEXT,
            codec TEXT, external INTEGER NOT NULL, disabled INTEGER NOT NULL, PRIMARY KEY(media_id, kind),
            FOREIGN KEY(media_id) REFERENCES recent_media(media_id) ON UPDATE NO ACTION ON DELETE CASCADE)""")
        db.execSQL("""CREATE TABLE IF NOT EXISTS media_external_subtitles (
            media_id TEXT NOT NULL, resource_id TEXT NOT NULL, title TEXT NOT NULL, PRIMARY KEY(media_id, resource_id),
            FOREIGN KEY(media_id) REFERENCES recent_media(media_id) ON UPDATE NO ACTION ON DELETE CASCADE)""")
        db.execSQL("""CREATE TABLE IF NOT EXISTS playback_resources (
            resource_id TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            extension TEXT NOT NULL, size_bytes INTEGER NOT NULL, font_family TEXT)""")
    }
}
