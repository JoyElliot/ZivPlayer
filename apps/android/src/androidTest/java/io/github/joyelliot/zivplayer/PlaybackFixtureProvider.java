// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.util.AtomicFile;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Test APK process has no target-app Kotlin runtime, so this provider uses Android/JDK only. */
public final class PlaybackFixtureProvider extends ContentProvider {
    private static final Set<String> FILES = new HashSet<>(Arrays.asList(
            "baseline-av.mp4", "tracks-subtitles.mkv", "external.ass", "external.srt"));

    @Override public boolean onCreate() { return true; }

    private synchronized File fixture(Uri uri) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        if (!FILES.contains(name) || uri.getPathSegments().size() != 1) {
            throw new FileNotFoundException("Unknown fixture");
        }
        if (getContext() == null) throw new IllegalStateException("Provider not attached");
        File file = new File(getContext().getCacheDir(), name);
        if (!file.isFile()) {
            AtomicFile output = new AtomicFile(file);
            FileOutputStream stream = null;
            try (InputStream input = getContext().getAssets().open(name)) {
                stream = output.startWrite();
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) stream.write(buffer, 0, count);
                output.finishWrite(stream);
            } catch (IOException failure) {
                output.failWrite(stream);
                FileNotFoundException reported = new FileNotFoundException("Cannot read fixture " + name);
                reported.initCause(failure);
                throw reported;
            }
        }
        return file;
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("Fixtures are read only");
        return ParcelFileDescriptor.open(fixture(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection,
            String[] selectionArgs, String sortOrder) {
        final File file;
        try { file = fixture(uri); }
        catch (FileNotFoundException failure) { throw new IllegalArgumentException(failure); }
        String[] columns = projection != null ? projection
                : new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor cursor = new MatrixCursor(columns);
        Object[] row = new Object[columns.length];
        for (int index = 0; index < columns.length; index++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[index])) row[index] = file.getName();
            else if (OpenableColumns.SIZE.equals(columns[index])) row[index] = file.length();
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override public String getType(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name != null && name.endsWith(".mp4")) return "video/mp4";
        if (name != null && name.endsWith(".mkv")) return "video/x-matroska";
        if (name != null && name.endsWith(".srt")) return "application/x-subrip";
        return "text/plain";
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException("Read only"); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException("Read only"); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException("Read only"); }
}
