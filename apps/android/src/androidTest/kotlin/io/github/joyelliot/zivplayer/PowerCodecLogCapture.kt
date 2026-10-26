// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Bounded, process-local in-memory evidence. No per-sample shell calls or disk writes. */
internal class PowerCodecLogCapture(private val ownerPid: Int) : AutoCloseable {
    private val openedWallMs = System.currentTimeMillis()
    private val child = ProcessBuilder("/system/bin/logcat", "-b", "main", "-b", "system", "-b", "crash",
        "-v", "epoch", "--pid", ownerPid.toString(), "-T", "1").redirectErrorStream(true).start()
    private val lines = ArrayList<String>()
    private var characters = 0
    @Volatile private var overflow = false
    @Volatile private var closing = false
    @Volatile private var ended = false
    @Volatile private var readerFailure: Throwable? = null
    private val reader = Thread({
        try {
            child.inputStream.bufferedReader().use { input ->
                while (true) {
                    val line = input.readLine() ?: break
                    synchronized(lines) {
                        if (characters + line.length + 1 <= 4 * 1024 * 1024) {
                            lines.add(line)
                            characters += line.length + 1
                        } else overflow = true
                    }
                }
            }
        } catch (failure: Throwable) {
            if (!closing) readerFailure = failure
        } finally { ended = true }
    }, "PowerCodecEvidence").apply { isDaemon = true; start() }

    data class Endpoint(val text: String, val wallMs: Long, val marker: String, val pid: Int) {
        fun metadata() = JSONObject().put("protocol", "v2-current-pid-stream").put("pid", pid)
            .put("wallMs", wallMs).put("marker", marker).put("markerSeen", true)
            .put("freshStatsWindowMs", 15000).put("requirePositiveRenderedFrames", true).put("characters", text.length)
    }

    fun endpoint(): Endpoint {
        val marker = "power-codec-evidence-${UUID.randomUUID()}"
        val wallMs = System.currentTimeMillis()
        Log.i("PowerCodecEvidence", marker)
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (SystemClock.elapsedRealtime() < deadline) {
            check(!overflow && readerFailure == null && !ended && child.isAlive) {
                "Decoder log stream unavailable, truncated, or ended: ${readerFailure?.message}"
            }
            val snapshot = synchronized(lines) { lines.toList() }
            if (snapshot.any { it.contains(marker) && entry(it)?.second == ownerPid }) {
                val text = snapshot.takeWhile { !it.contains(marker) }.filter { line ->
                    entry(line)?.let { it.second == ownerPid && it.first >= openedWallMs } ?: true
                }.joinToString("\n", postfix = "\n") + snapshot.first { it.contains(marker) } + "\n"
                return Endpoint(text, wallMs, marker, ownerPid)
            }
            SystemClock.sleep(10)
        }
        error("No current-process marker in decoder log stream; log access is not verified.")
    }

    fun rawText(): String = synchronized(lines) { lines.joinToString("\n", postfix = "\n") }

    override fun close() {
        closing = true
        child.destroy()
        if (!child.waitFor(3, TimeUnit.SECONDS)) {
            child.destroyForcibly()
            check(child.waitFor(3, TimeUnit.SECONDS)) { "Decoder log child did not exit." }
        }
        reader.join(3000)
        check(!reader.isAlive) { "Decoder log reader did not exit." }
    }

    companion object {
        private val epochLine = Regex("^\\s*(\\d+\\.\\d+)\\s+(\\d+)\\s+\\d+\\s+[VDIWEFAS]\\s+.*$")
        fun entry(line: String): Pair<Long, Int>? = epochLine.matchEntire(line)?.let {
            (it.groupValues[1].toDouble() * 1000).toLong() to it.groupValues[2].toInt()
        }
        fun freshStats(endpoint: Endpoint): List<String> = endpoint.text.lineSequence().filter { line ->
            val stamp = entry(line)
            stamp != null && stamp.second == endpoint.pid && stamp.first in (endpoint.wallMs - 15000)..endpoint.wallMs &&
                line.contains("[video-debug-dec] Stats ") &&
                (Regex("\\bRender:(\\d+)").find(line)?.groupValues?.get(1)?.toLongOrNull() ?: 0) > 0
        }.toList()
    }
}
