// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.library

import io.github.joyelliot.zivplayer.core.media.LibraryFolder
import io.github.joyelliot.zivplayer.core.media.LibraryMedia
import io.github.joyelliot.zivplayer.core.media.MediaFileClassifier

/** A displayed directory is scoped to its SAF grant, even when two roots share a name. */
internal data class LibraryDirectory(
    val root: LibraryFolder,
    val path: String,
    val media: List<LibraryMedia>,
) {
    val key: String get() = "${root.id.length}:${root.id}:$path"
    val name: String get() = path.substringAfterLast('/').ifBlank { root.name }
    val audioCount: Int = media.count { MediaFileClassifier.isAudio(it.title, it.mimeType) }
}

internal fun LibraryMedia.directoryPath(): String = relativePath.substringBeforeLast('/', "")

internal fun libraryDirectories(folders: List<LibraryFolder>, media: List<LibraryMedia>): List<LibraryDirectory> {
    val roots = folders.associateBy { it.id }
    return media.groupBy { it.folderId to it.directoryPath() }.mapNotNull { (location, files) ->
        roots[location.first]?.let { LibraryDirectory(it, location.second, files) }
    }
}
