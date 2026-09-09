// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.abs

data class SeekPreviewFrame(val token: Long, val bitmap: Bitmap, val positionMs: Long,
                            val commitReady: Boolean = false)

/** One local visual preview. Playback, audio focus and history remain owned by the service. */
internal class ScrubPreviewRenderer(private val context: Context, private val uri: Uri,
    private val onFrame: (ScrubFrameExtractor.Frame) -> Unit, private val onFailure: () -> Unit) : AutoCloseable {
    private data class Target(val positionUs: Long, val atMs: Long, val reverseLeadUs: Long = 0,
                              val speedUsPerMs: Long = 0)
    private val executor = Executors.newSingleThreadExecutor { task -> Thread(task, "ZivScrubDecoder") }
    private val scheduling = Any()
    private val handler = Handler(Looper.getMainLooper())
    private val closed = AtomicBoolean()
    private val running = AtomicBoolean()
    private val latest = AtomicReference<Target?>()
    private val delivery = AtomicReference<ScrubFrameExtractor.Frame?>()
    private val deliveryPosted = AtomicBoolean()
    @Volatile private var decoder: ScrubFrameExtractor? = null
    private var published = false
    private var lastPublishedUs = 0L

    fun request(positionMs: Long) {
        if (closed.get()) return
        val previous = latest.get()
        val now = SystemClock.elapsedRealtime()
        val speed = if (previous == null) 0 else if (positionMs * 1_000 == previous.positionUs)
            previous.speedUsPerMs else abs(positionMs * 1_000 - previous.positionUs) / (now - previous.atMs).coerceAtLeast(1)
        val target = Target(positionMs * 1_000, now,
            if (previous != null && positionMs * 1_000 < previous.positionUs) {
                (speed * 650).coerceIn(2_000_000, 8_000_000)
            } else if (previous != null && positionMs * 1_000 == previous.positionUs) previous.reverseLeadUs else 0,
            speedUsPerMs = speed)
        latest.set(target)
        decoder?.cached(target.positionUs)?.let(::deliver)
        startWorker()
    }

    private fun startWorker(): Unit = synchronized(scheduling) {
        if (closed.get() || !running.compareAndSet(false, true)) return
        executor.execute {
            var processed: Target? = null
            var failed = false
            try {
                val active = decoder ?: ScrubFrameExtractor(context, uri).also { decoder = it }
                while (!closed.get()) {
                    val target = latest.get() ?: break
                    if (target === processed) break
                    val decodeStartedAt = SystemClock.elapsedRealtime()
                    val workBudgetMs = (10_000_000L / target.speedUsPerMs.coerceAtLeast(1)).coerceIn(250, 1_000)
                    val frame = active.frameAt(target.positionUs,
                        cancelled = {
                            closed.get() || latest.get()?.let { wanted ->
                                val reversed = (wanted.reverseLeadUs > 0) != (target.reverseLeadUs > 0)
                                // A 3s distance alone gives a 40x sweep only 75ms
                                // before another flush. Allow useful same-direction
                                // decoding to finish a bounded slice. Slower drags
                                // give long GOPs more time; reversal and release
                                // still interrupt immediately.
                                reversed || (SystemClock.elapsedRealtime() - decodeStartedAt >= workBudgetMs &&
                                    abs(wanted.positionUs - target.positionUs) > 3_000_000)
                            } == true
                        },
                        interim = { intermediate ->
                            val wanted = latest.get()
                            if (wanted != null) deliver(active.cached(wanted.positionUs) ?: intermediate)
                        })
                    if (frame != null) deliver(frame)
                    processed = target
                    if (latest.get() === target && target.reverseLeadUs > 0) {
                        val before = (target.positionUs - target.reverseLeadUs).coerceAtLeast(0)
                        if (active.cached(before) == null) active.frameAt(before,
                            // Speculative work must yield to either direction of
                            // new input, without the foreground decode allowance.
                            cancelled = { closed.get() || latest.get() !== target })
                    }
                }
            } catch (failure: Exception) {
                failed = true
                android.util.Log.w("ZivSeekPreview", "Independent preview unavailable; using the playback engine.", failure)
                handler.post { if (!closed.get()) onFailure() }
            } finally {
                running.set(false)
                if (!closed.get() && !failed && latest.get() !== processed) startWorker()
            }
        }
    }

    private fun deliver(frame: ScrubFrameExtractor.Frame) {
        delivery.set(frame)
        if (!deliveryPosted.compareAndSet(false, true)) return
        handler.post {
            deliveryPosted.set(false)
            val wanted = latest.get()
            val candidate = delivery.getAndSet(null)
            if (!closed.get() && wanted != null && candidate != null) {
                val exact = decoder?.cached(wanted.positionUs)
                val visible = exact ?: candidate
                // Fast drags can advance faster than full-GOP decoding. Keep presenting
                // intermediate frames in the gesture's direction instead of freezing while
                // waiting to catch up; the release still performs one exact main-engine seek.
                val followsGesture = if (wanted.reverseLeadUs > 0) visible.positionUs <= lastPublishedUs
                    else visible.positionUs >= lastPublishedUs && visible.positionUs <= wanted.positionUs + 150_000
                if (!published || exact != null || followsGesture) {
                    published = true
                    lastPublishedUs = visible.positionUs
                    onFrame(visible)
                }
            }
        }
    }

    override fun close() = synchronized(scheduling) {
        if (!closed.compareAndSet(false, true)) return
        latest.set(null)
        delivery.set(null)
        executor.execute { decoder?.close(); decoder = null }
        executor.shutdown()
    }
}
