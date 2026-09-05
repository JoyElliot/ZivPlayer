// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test

class BoundedBlockingIoTest {
    @Test fun cancellationReturnsWhileUncooperativeProviderStillOwnsWorker() = runBlocking {
        val provider = BoundedBlockingIo("ZivProviderTest")
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        try {
            val read = async { provider.run { _ -> entered.countDown(); release.await(); "late result" } }
            withContext(Dispatchers.IO) { assertTrue(entered.await(5, TimeUnit.SECONDS)) }
            withTimeout(2_000) { read.cancelAndJoin() }
            assertTrue(read.isCancelled)
            // The same worker becomes usable when the provider eventually returns.
            release.countDown()
            assertEquals("next", withTimeout(2_000) { provider.run { "next" } })
        } finally { release.countDown() }
    }
}
