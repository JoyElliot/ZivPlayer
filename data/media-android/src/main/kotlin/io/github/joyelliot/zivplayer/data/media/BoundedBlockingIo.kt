// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import java.io.IOException
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine

/** A provider may ignore cancellation. A stuck call occupies at most one daemon worker. */
internal class BoundedBlockingIo(name: String) {
    private val executor = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(1),
        { task -> Thread(task, name).apply { isDaemon = true } }, ThreadPoolExecutor.AbortPolicy())

    suspend fun <T> run(onCancel: () -> Unit = {}, block: (isActive: () -> Boolean) -> T): T =
        suspendCancellableCoroutine { continuation ->
            val task = Runnable {
                if (!continuation.isActive) return@Runnable
                try { continuation.resume(block { continuation.isActive })
                } catch (failure: Exception) { continuation.resumeWithException(failure) }
            }
            continuation.invokeOnCancellation {
                executor.remove(task)
                runCatching(onCancel)
            }
            try { executor.execute(task) } catch (_: RejectedExecutionException) {
                continuation.resumeWithException(IOException("文件提供者仍在响应上次操作，请稍后重试"))
            }
        }
}
