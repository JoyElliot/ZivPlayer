// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/** SAF-shaped read-only fixture source, present only in the instrumentation APK. */
class PlaybackFixtureProvider : ContentProvider() {
    override fun onCreate() = true

    private fun fixture(uri: Uri): File {
        val name = uri.lastPathSegment
        if (name !in FILES || uri.pathSegments.size != 1) throw FileNotFoundException("Unknown fixture")
        val context = checkNotNull(context)
        return synchronized(this) {
            File(context.cacheDir, checkNotNull(name)).also { file ->
                if (!file.isFile) context.assets.open(name).use { input -> file.outputStream().use(input::copyTo) }
            }
        }
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        if (mode != "r") throw FileNotFoundException("Fixtures are read only")
        return ParcelFileDescriptor.open(fixture(uri), ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?,
        selectionArgs: Array<out String>?, sortOrder: String?): Cursor {
        val file = fixture(uri)
        val columns = projection ?: arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
        return MatrixCursor(columns).apply {
            addRow(columns.map { when (it) {
                OpenableColumns.DISPLAY_NAME -> file.name
                OpenableColumns.SIZE -> file.length()
                else -> null
            } })
        }
    }

    override fun getType(uri: Uri): String = when (uri.lastPathSegment?.substringAfterLast('.')) {
        "mp4" -> "video/mp4"
        "mkv" -> "video/x-matroska"
        "srt" -> "application/x-subrip"
        else -> "text/plain"
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = error("Read only")
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = error("Read only")
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = error("Read only")

    private companion object {
        val FILES = setOf("baseline-av.mp4", "tracks-subtitles.mkv", "external.ass", "external.srt")
    }
}
