// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.PlaybackCheckpoint
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.Milliseconds
import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackResumePolicyTest {
    @Test
    fun incompleteCheckpointResumesAtRecordedPosition() {
        assertEquals(42_000L, checkpoint(positionMs = 42_000L).resumePositionMs())
    }

    @Test
    fun knownDurationClampsResumePosition() {
        assertEquals(
            30_000L,
            checkpoint(positionMs = 42_000L, durationMs = 30_000L).resumePositionMs(),
        )
    }

    @Test
    fun completedOrMissingCheckpointStartsFromBeginning() {
        assertEquals(0L, checkpoint(positionMs = 42_000L, completed = true).resumePositionMs())
        assertEquals(0L, null.resumePositionMs())
    }

    @Test
    fun observedCompletionOverridesAStaleIncompleteCheckpoint() {
        assertEquals(
            0L,
            checkpoint(positionMs = 42_000L).resumePositionMs(startFromBeginning = true),
        )
    }
}

private fun checkpoint(
    positionMs: Long,
    durationMs: Long? = null,
    completed: Boolean = false,
): PlaybackCheckpoint = PlaybackCheckpoint(
    mediaId = MediaId("media"),
    position = Milliseconds(positionMs),
    duration = durationMs?.let(::Milliseconds),
    completed = completed,
    updatedAt = EpochMilliseconds(1L),
)
