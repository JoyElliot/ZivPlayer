// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import io.github.joyelliot.zivplayer.core.model.Milliseconds
import io.github.joyelliot.zivplayer.core.player.runtime.BackendSeekRequest
import io.github.joyelliot.zivplayer.core.player.runtime.LoadGeneration
import io.github.joyelliot.zivplayer.core.player.runtime.SeekGeneration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SeekCorrelationTest {
    @Test
    fun completionRequiresAnArmingSignalAndPositionEpochPersists() {
        val correlation = SeekCorrelation()
        val request = request(load = 1, seek = 1, position = 5_000)

        correlation.begin(request)
        assertNull(correlation.positionEpoch(request.generation))
        assertNull(correlation.complete(request.generation))

        correlation.armCompletion(request.generation)
        assertEquals(request.seekGeneration, correlation.positionEpoch(request.generation))
        assertEquals(request, correlation.complete(request.generation))
        assertEquals(request.seekGeneration, correlation.positionEpoch(request.generation))
        assertNull(correlation.complete(request.generation))
    }

    @Test
    fun delayedFalseSignalCannotCompleteTheNextSeekBeforeItIsArmed() {
        val correlation = SeekCorrelation()
        val first = request(load = 1, seek = 1, position = 5_000)
        val second = request(load = 1, seek = 2, position = 12_000)

        correlation.begin(first)
        correlation.armCompletion(first.generation)
        assertEquals(first, correlation.complete(first.generation))

        correlation.begin(second)
        assertEquals(first.seekGeneration, correlation.positionEpoch(second.generation))
        assertNull(correlation.complete(second.generation))
        correlation.armCompletion(second.generation)
        assertEquals(second.seekGeneration, correlation.positionEpoch(second.generation))
        assertEquals(second, correlation.complete(second.generation))
        assertEquals(second.seekGeneration, correlation.positionEpoch(second.generation))
    }

    @Test
    fun resetDropsPendingSeekAndPositionEpoch() {
        val correlation = SeekCorrelation()
        val request = request(load = 3, seek = 4, position = 9_000)
        correlation.begin(request)

        correlation.reset()

        assertNull(correlation.complete(request.generation))
        assertNull(correlation.positionEpoch(request.generation))
    }

    private fun request(load: Long, seek: Long, position: Long): BackendSeekRequest =
        BackendSeekRequest(
            generation = LoadGeneration(load),
            seekGeneration = SeekGeneration(seek),
            position = Milliseconds(position),
        )
}
