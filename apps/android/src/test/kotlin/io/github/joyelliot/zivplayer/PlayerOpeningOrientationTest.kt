// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test

class PlayerOpeningOrientationTest {
    @Test fun videoUsesDisplayAspectRelativeToTheEntryDirection() {
        for (entryLandscape in listOf(false, true)) for (aspect in listOf(16f / 9, 9f / 16)) {
            val policy = PlayerOpeningOrientation()
            assertEquals((aspect > 1f) != entryLandscape, policy.resolve("a", "uri:a", "a", aspect, entryLandscape))
        }
    }

    @Test fun unknownDimensionsAndPreviousMediaNeverChooseTheDefault() {
        val policy = PlayerOpeningOrientation()
        policy.open("uri:b", null, "old")
        assertNull(policy.resolve("old", "uri:a", "a", 16f / 9, false))
        assertNull(policy.resolve("new", "uri:b", "b", null, false))
        assertEquals(false, policy.resolve("new", "uri:b", "b", 9f / 16, false))
    }

    @Test fun manualTurnWhileNewMediaIsLoadingWinsOverLateDimensions() {
        val policy = PlayerOpeningOrientation()
        policy.open("uri:b", null, "old")
        policy.manual("old", false)
        assertEquals(false, policy.resolve("new", "uri:b", "b", 16f / 9, false))
        assertNull(policy.resolve("new", "uri:b", "b", 9f / 16, false))
        assertEquals(false, policy.resolve("next", "uri:c", "c", 9f / 16, false))
    }

    @Test fun reopeningTheSameFileWaitsForItsNewPlaybackIdentity() {
        val policy = PlayerOpeningOrientation()
        policy.open("uri:a", "a", "old")
        assertNull(policy.resolve("old", "uri:a", "a", 16f / 9, false))
        policy.manual("old", false)
        assertEquals(false, policy.resolve("new", "uri:a", "a", 16f / 9, false))
    }

    @Test fun miniReopeningKeepsManualDirectionWhileNewItemsGetFreshDefaults() {
        val policy = PlayerOpeningOrientation()
        assertEquals(true, policy.resolve("a", "uri:a", "a", 16f / 9, false))
        policy.manual("a", false)
        assertNull(policy.resolve("a", "uri:a", "a", 9f / 16, false))
        policy.open("uri:a", "a")
        assertEquals(false, policy.resolve("a", "uri:a", "a", 16f / 9, false))
        policy.open("uri:a", "a")
        assertEquals(true, policy.resolve("a", "uri:a", "a", 16f / 9, true))
        assertEquals(false, policy.resolve("b", "uri:b", "b", 9f / 16, false))
    }

    @Test fun squareVideosKeepTheEntryOrientation() {
        assertEquals(false, PlayerOpeningOrientation().resolve("a", null, "a", 1f, true))
    }

    @Test fun queueCanAdvanceBeforeTheOpenedItemHasDimensions() {
        val policy = PlayerOpeningOrientation()
        policy.open("uri:a", null, "old")
        assertNull(policy.resolve("a", "uri:a", "a", null, false))
        assertEquals(true, policy.resolve("b", "uri:b", "b", 16f / 9, false))
    }

    @Test fun manualChoiceBindsToTheLoadingItemRatherThanTheNextQueueItem() {
        val policy = PlayerOpeningOrientation()
        policy.open("uri:a", null, "old")
        policy.manual("old", false)
        assertEquals(false, policy.resolve("a", "uri:a", "a", null, false))
        assertNull(policy.resolve("a", "uri:a", "a", 16f / 9, false))
        assertEquals(true, policy.resolve("b", "uri:b", "b", 16f / 9, false))
    }

    @Test fun audioAndTerminalUnknownGeometryCanOpenWithoutWaitingForever() {
        val policy = PlayerOpeningOrientation()
        policy.open("uri:a", null)
        assertNull(policy.resolve("a", "uri:a", "a", null, false))
        assertEquals(false, policy.resolve("a", "uri:a", "a", null, false, readyWithoutGeometry = true))
    }

    @Test fun newPlaybackOfTheSameFileDoesNotInheritTheOldSessionChoice() {
        val policy = PlayerOpeningOrientation()
        policy.resolve("old", "uri:a", "a", 16f / 9, false)
        policy.manual("old", false)
        policy.open("uri:a", "a", "old")
        assertNull(policy.resolve("old", "uri:a", "a", 16f / 9, false))
        assertEquals(true, policy.resolve("new", "uri:a", "a", 16f / 9, false))
    }
}
