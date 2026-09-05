// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.playback

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TrackRestoreWaitTest {
    @Test fun cancellationReleasesPlayWithoutNativeReadiness() = runTest {
        val restoration = CompletableDeferred<Unit>()
        val started = CompletableDeferred<Unit>()
        val disposed = CompletableDeferred<Unit>()
        val result = async {
            awaitPreparationOrRestoreCancellation(restoration) {
                started.complete(Unit)
                try { awaitCancellation() } finally { disposed.complete(Unit) }
            }
        }
        started.await()
        restoration.complete(Unit)
        assertFalse(result.await())
        assertTrue(disposed.isCompleted)
    }

    @Test fun readinessCanProceedBeforeRestorationCompletes() = runTest {
        val restoration = CompletableDeferred<Unit>()
        assertTrue(awaitPreparationOrRestoreCancellation(restoration) {})
        assertFalse(restoration.isCompleted)
    }
}
