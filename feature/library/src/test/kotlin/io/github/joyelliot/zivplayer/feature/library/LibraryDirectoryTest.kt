// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.library

import io.github.joyelliot.zivplayer.core.media.*
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LibraryDirectoryTest {
    @Test fun sameNamedDirectoriesStayScopedToTheirRootAndFullParentPath() {
        val roots = listOf(root("a"), root("b"))
        val media = listOf(file("a", "Season 1/Extras/one.mp4"), file("a", "Season 2/Extras/two.mp4"),
            file("b", "Season 1/Extras/three.mp4"), file("a", "Season 1/Extras/four.mp4"))
        val groups = libraryDirectories(roots, media)
        assertEquals(3, groups.size)
        assertEquals(3, groups.map { it.key }.toSet().size)
        assertTrue(groups.all { it.name == "Extras" })
        assertEquals(2, groups.single { it.root.id == "a" && it.path == "Season 1/Extras" }.media.size)
    }

    @Test fun rootFilesAndSubdirectoryFilesHaveDistinctQueues() {
        val media = listOf(file("a", "one.mp4"), file("a", "Child/two.mp4"), file("removed", "three.mp4"))
        val groups = libraryDirectories(listOf(root("a")), media)
        assertEquals(2, groups.size)
        assertEquals(listOf(media[0]), groups.single { it.path.isEmpty() }.media)
        assertEquals("Storage", groups.single { it.path.isEmpty() }.name)
        assertEquals(listOf(media[1]), groups.single { it.path == "Child" }.media)
    }

    private fun root(id: String) = LibraryFolder(id, DurableMediaUri("content://test/tree/$id"), "Storage",
        LibraryFolderStatus.READY, null, 0)
    private fun file(root: String, path: String) = LibraryMedia(root, DurableMediaUri("content://test/$root/$path"),
        path.substringAfterLast('/'), "video/mp4", path, null, null)
}
