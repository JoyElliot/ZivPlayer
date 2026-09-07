// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import org.junit.Assert.*
import org.junit.Test

class TemporaryPlaybackSpeedTest {
    private val identity = SpeedPlaybackIdentity(1, "media", 5, 0)

    @Test fun releaseRestoresOriginalSpeedExactlyOnce() {
        val owner = TemporaryPlaybackSpeed()
        val token = owner.begin(identity, 1.25f)
        assertTrue(owner.owns(token, identity))
        assertEquals(1.25f, owner.finish(token, identity))
        assertNull(owner.finish(token, identity))
    }

    @Test fun staleGestureCannotOverwriteAnotherOccurrenceOrConnection() {
        listOf(identity.copy(connection = 2), identity.copy(sequence = 6), identity.copy(index = 1), identity.copy(mediaId = "other")).forEach { newer ->
            val owner = TemporaryPlaybackSpeed()
            val token = owner.begin(identity, 1.25f)
            assertFalse(owner.owns(token, newer))
            assertNull(owner.finish(token, newer))
        }
    }

    @Test fun newSelectionAndNewGestureInvalidateOlderRelease() {
        val owner = TemporaryPlaybackSpeed()
        val old = owner.begin(identity, 1f)
        val current = owner.begin(identity, 1.5f)
        assertNull(owner.finish(old, identity))
        assertEquals(1.5f, owner.finish(current, identity))
        val explicit = owner.begin(identity, 1f)
        owner.invalidate()
        assertNull(owner.finish(explicit, identity))
    }
}
