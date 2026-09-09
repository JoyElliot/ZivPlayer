// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.Context
import android.graphics.Bitmap
import android.graphics.SurfaceTexture
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.opengl.EGL14
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import android.view.Surface
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.TreeMap
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.abs

/** A thread-confined, video-only decoder. It owns its source, codec and offscreen Surface. */
internal class ScrubFrameExtractor(context: Context, uri: Uri) : AutoCloseable {
    data class Frame(val positionUs: Long, val bitmap: Bitmap)
    private val extractor = MediaExtractor()
    private val ownerThread = Thread.currentThread()
    private val codecFormat: MediaFormat
    private var codec: MediaCodec? = null
    private var output: FrameReadback? = null
    private val info = MediaCodec.BufferInfo()
    private val cache = TreeMap<Long, Frame>()
    private var lastPts = -1L
    private var lastCachedPts = Long.MIN_VALUE
    private var inputEnded = false
    private var inputStarted = false
    private var outputStarted = false
    private var dirty = false
    private var closed = false
    private var lastFrame: Frame? = null
    private var durationUs = Long.MAX_VALUE
    val decoderName: String

    init {
        try {
            check(Looper.myLooper() != Looper.getMainLooper()) { "Preview decoding requires a dedicated worker" }
            check(EGL14.eglGetCurrentContext() == EGL14.EGL_NO_CONTEXT) { "The preview worker must not borrow another renderer's context" }
            extractor.setDataSource(context, uri, null)
            val track = (0 until extractor.trackCount).first {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true
            }
            val format = extractor.getTrackFormat(track)
            require(!format.containsKey(MediaFormat.KEY_COLOR_TRANSFER) || format.getInteger(MediaFormat.KEY_COLOR_TRANSFER) !in 6..7) { "HDR preview uses the main renderer" }
            require(!format.containsKey(MediaFormat.KEY_COLOR_STANDARD) || format.getInteger(MediaFormat.KEY_COLOR_STANDARD) != 6) { "Wide color preview uses the main renderer" }
            require(!format.containsKey("rotation-degrees") || format.getInteger("rotation-degrees") % 360 == 0) { "Rotated preview uses the main renderer" }
            require(!format.containsKey("sar-width") || !format.containsKey("sar-height") ||
                format.getInteger("sar-width") == format.getInteger("sar-height")) { "Non-square pixels use the main renderer" }
            codecFormat = format
            if (format.containsKey(MediaFormat.KEY_DURATION)) durationUs = format.getLong(MediaFormat.KEY_DURATION)
            extractor.selectTrack(track)
            val width = format.getInteger(MediaFormat.KEY_WIDTH)
            val height = format.getInteger(MediaFormat.KEY_HEIGHT)
            val readback = FrameReadback(width, height)
            output = readback
            val decoder = MediaCodec.createDecoderByType(checkNotNull(format.getString(MediaFormat.KEY_MIME)))
            codec = decoder
            decoderName = decoder.name
            decoder.configure(format, readback.surface, null, 0)
            decoder.start()
        } catch (failure: Throwable) {
            close()
            throw failure
        }
    }

    /** Approximate cached frames have at most 150ms media-time error; commits use the main engine. */
    fun cached(positionUs: Long): Frame? = synchronized(cache) {
        listOfNotNull(cache.floorEntry(positionUs)?.value, cache.ceilingEntry(positionUs)?.value)
            .minByOrNull { abs(it.positionUs - positionUs) }?.takeIf { abs(it.positionUs - positionUs) <= CACHE_STEP_US }
    }

    fun frameAt(positionUs: Long, cancelled: () -> Boolean = { false },
                interim: (Frame) -> Unit = {}): Frame? {
        check(Thread.currentThread() === ownerThread && !closed)
        val targetUs = positionUs.coerceIn(0, (durationUs - 1).coerceAtLeast(0))
        cached(targetUs)?.let { return it }
        val decoder = checkNotNull(codec)
        val readback = checkNotNull(output)
        if (dirty || lastPts < 0 || targetUs < lastPts || targetUs - lastPts > 2_000_000) {
            extractor.seekTo(targetUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
            if (outputStarted) decoder.flush() else if (inputStarted) {
                // An early flush can lose the codec's CSD before its first output/format.
                decoder.stop()
                decoder.configure(codecFormat, readback.surface, null, 0)
                decoder.start()
            }
            inputEnded = false
            inputStarted = false
            dirty = false
            lastCachedPts = Long.MIN_VALUE
        }
        val deadline = SystemClock.elapsedRealtime() + 5_000
        var first = true
        while (!cancelled()) {
            check(SystemClock.elapsedRealtime() < deadline) { "Preview decoder timed out" }
            if (!inputEnded) {
                val input = decoder.dequeueInputBuffer(0)
                if (input >= 0) {
                    val buffer = checkNotNull(decoder.getInputBuffer(input))
                    val size = extractor.readSampleData(buffer, 0)
                    if (size < 0) {
                        decoder.queueInputBuffer(input, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputEnded = true
                    } else {
                        decoder.queueInputBuffer(input, 0, size, extractor.sampleTime, 0)
                        extractor.advance()
                        inputStarted = true
                    }
                }
            }
            val index = decoder.dequeueOutputBuffer(info, 1_000)
            if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                outputStarted = true
                val changed = decoder.outputFormat
                val w = changed.getInteger(MediaFormat.KEY_WIDTH)
                val h = changed.getInteger(MediaFormat.KEY_HEIGHT)
                val cropW = if (changed.containsKey("crop-right")) changed.getInteger("crop-right") - changed.getInteger("crop-left") + 1 else w
                val cropH = if (changed.containsKey("crop-bottom")) changed.getInteger("crop-bottom") - changed.getInteger("crop-top") + 1 else h
                val ratio = codecFormat.getInteger(MediaFormat.KEY_WIDTH).toDouble() / codecFormat.getInteger(MediaFormat.KEY_HEIGHT)
                require(abs(cropW.toDouble() / cropH - ratio) < 0.03) { "Dynamic video geometry uses the main renderer" }
                continue
            }
            if (index < 0) continue
            outputStarted = true
            val eos = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
            if (eos && info.size == 0) { decoder.releaseOutputBuffer(index, false); return lastFrame }
            if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) { decoder.releaseOutputBuffer(index, false); continue }
            lastPts = info.presentationTimeUs
            val reached = lastPts >= targetUs || eos
            val keep = first || reached || lastPts >= durationUs - 200_000 ||
                lastCachedPts == Long.MIN_VALUE || lastPts - lastCachedPts >= CACHE_STEP_US
            val serial = if (keep) readback.serial() else 0
            decoder.releaseOutputBuffer(index, keep)
            if (keep) {
                val bitmap = readback.read(serial, lastPts, cancelled)
                if (bitmap == null) { dirty = true; return null }
                val frame = Frame(lastPts, bitmap)
                lastFrame = frame
                synchronized(cache) {
                    cache[lastPts] = frame
                    while (cache.size > MAX_CACHED_FRAMES) {
                        val firstKey = cache.firstKey()
                        val lastKey = cache.lastKey()
                        cache.remove(if (abs(firstKey - targetUs) > abs(lastKey - targetUs)) firstKey else lastKey)
                    }
                }
                lastCachedPts = lastPts
                interim(frame)
                if (reached) return frame
            }
            first = false
        }
        dirty = true
        return null
    }

    override fun close() {
        check(Thread.currentThread() === ownerThread)
        if (closed) return
        closed = true
        codec?.let { runCatching { it.stop() }; runCatching { it.release() } }
        codec = null
        runCatching { extractor.release() }
        output?.close()
        output = null
        lastFrame = null
        synchronized(cache) { cache.clear() }
    }

    companion object {
        private const val CACHE_STEP_US = 150_000L
        private const val MAX_CACHED_FRAMES = 80 // <= 42 MiB of 480x270 RGBA frames.
    }
}

/** GPU downscaling avoids CPU YUV/10-bit conversion and keeps cached frames small. */
private class FrameReadback(sourceWidth: Int, sourceHeight: Int) : AutoCloseable {
    private val ownerThread = Thread.currentThread()
    private val width = if (sourceWidth >= sourceHeight) 480 else (480L * sourceWidth / sourceHeight).toInt().coerceAtLeast(2)
    private val height = if (sourceWidth >= sourceHeight) (480L * sourceHeight / sourceWidth).toInt().coerceAtLeast(2) else 480
    private val display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
    private var initialized = false
    private var context: android.opengl.EGLContext = EGL14.EGL_NO_CONTEXT
    private var pbuffer: android.opengl.EGLSurface = EGL14.EGL_NO_SURFACE
    private var textureId = 0
    private var texture: SurfaceTexture? = null
    private var ownedSurface: Surface? = null
    val surface: Surface get() = checkNotNull(ownedSurface)
    private var callbacks: HandlerThread? = null
    private var closed = false
    private val shaders = mutableListOf<Int>()
    private val frameSerial = AtomicLong()
    private val matrix = FloatArray(16)
    private val pixels = ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder())
    private val vertices: FloatBuffer = ByteBuffer.allocateDirect(16 * 4).order(ByteOrder.nativeOrder()).asFloatBuffer().apply {
        // Invert Y while drawing so glReadPixels' bottom row becomes the Bitmap's top row.
        put(floatArrayOf(-1f, -1f, 0f, 1f, 1f, -1f, 1f, 1f, -1f, 1f, 0f, 0f, 1f, 1f, 1f, 0f)); position(0)
    }
    private var program = 0
    init {
      try {
        require(sourceWidth > 0 && sourceHeight > 0)
        check(display != EGL14.EGL_NO_DISPLAY)
        check(EGL14.eglInitialize(display, IntArray(2), 0, IntArray(2), 0))
        initialized = true
        val configs = arrayOfNulls<android.opengl.EGLConfig>(1)
        check(EGL14.eglChooseConfig(display, intArrayOf(EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8,
            EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8, EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
            EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT, EGL14.EGL_NONE), 0, configs, 0, 1, IntArray(1), 0))
        val config = checkNotNull(configs[0])
        context = EGL14.eglCreateContext(display, config, EGL14.EGL_NO_CONTEXT,
            intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE), 0)
        check(context != EGL14.EGL_NO_CONTEXT)
        pbuffer = EGL14.eglCreatePbufferSurface(display, config,
            intArrayOf(EGL14.EGL_WIDTH, width, EGL14.EGL_HEIGHT, height, EGL14.EGL_NONE), 0)
        check(pbuffer != EGL14.EGL_NO_SURFACE)
        check(EGL14.eglMakeCurrent(display, pbuffer, pbuffer, context))
        val names = IntArray(1)
        GLES20.glGenTextures(1, names, 0)
        textureId = names[0]
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
        val callbackThread = HandlerThread("ZivScrubFrames").apply { start() }
        callbacks = callbackThread
        val producer = SurfaceTexture(textureId)
        texture = producer
        producer.apply {
            setDefaultBufferSize(sourceWidth, sourceHeight)
            setOnFrameAvailableListener({ frameSerial.incrementAndGet() }, Handler(callbackThread.looper))
        }
        ownedSurface = Surface(producer)
        fun shader(type: Int, code: String): Int = GLES20.glCreateShader(type).also {
            shaders += it
            GLES20.glShaderSource(it, code); GLES20.glCompileShader(it)
            val status = IntArray(1); GLES20.glGetShaderiv(it, GLES20.GL_COMPILE_STATUS, status, 0)
            check(status[0] != 0) { GLES20.glGetShaderInfoLog(it) }
        }
        val vertex = shader(GLES20.GL_VERTEX_SHADER,
            "attribute vec2 pos; attribute vec2 uv; uniform mat4 transform; varying vec2 tex; void main(){ gl_Position=vec4(pos,0.,1.); tex=(transform*vec4(uv,0.,1.)).xy; }")
        val fragment = shader(GLES20.GL_FRAGMENT_SHADER,
            "#extension GL_OES_EGL_image_external : require\nprecision mediump float; uniform samplerExternalOES image; varying vec2 tex; void main(){gl_FragColor=texture2D(image,tex);}")
        program = GLES20.glCreateProgram()
        GLES20.glAttachShader(program, vertex); GLES20.glAttachShader(program, fragment); GLES20.glLinkProgram(program)
        GLES20.glDeleteShader(vertex); GLES20.glDeleteShader(fragment)
        shaders.clear()
        val status = IntArray(1); GLES20.glGetProgramiv(program, GLES20.GL_LINK_STATUS, status, 0)
        check(status[0] != 0) { GLES20.glGetProgramInfoLog(program) }
      } catch (failure: Throwable) {
        close()
        throw failure
      }
    }
    fun serial(): Long = frameSerial.get()
    fun read(previous: Long, expectedPtsUs: Long, cancelled: () -> Boolean): Bitmap? {
        check(Thread.currentThread() === ownerThread && !closed)
        val producer = checkNotNull(texture)
        val deadline = SystemClock.elapsedRealtime() + 1_000
        var consumed = previous
        while (true) {
            if (cancelled()) return null
            check(SystemClock.elapsedRealtime() < deadline) { "Preview Surface did not produce a frame" }
            val available = frameSerial.get()
            if (available > consumed) {
                producer.updateTexImage()
                consumed = available
                // MediaCodec's default Surface timestamp is its PTS (Android M+).
                // Old callbacks/buffers left by cancellation must never label a new target.
                if (abs(producer.timestamp / 1_000 - expectedPtsUs) <= 1_000) break
            }
            SystemClock.sleep(1)
        }
        producer.getTransformMatrix(matrix)
        GLES20.glViewport(0, 0, width, height)
        GLES20.glUseProgram(program)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
        GLES20.glUniform1i(GLES20.glGetUniformLocation(program, "image"), 0)
        GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(program, "transform"), 1, false, matrix, 0)
        val pos = GLES20.glGetAttribLocation(program, "pos")
        val uv = GLES20.glGetAttribLocation(program, "uv")
        vertices.position(0); GLES20.glVertexAttribPointer(pos, 2, GLES20.GL_FLOAT, false, 16, vertices)
        vertices.position(2); GLES20.glVertexAttribPointer(uv, 2, GLES20.GL_FLOAT, false, 16, vertices)
        GLES20.glEnableVertexAttribArray(pos); GLES20.glEnableVertexAttribArray(uv)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        pixels.clear()
        GLES20.glReadPixels(0, 0, width, height, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, pixels)
        check(GLES20.glGetError() == GLES20.GL_NO_ERROR) { "Preview GPU readback failed" }
        return Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also { pixels.rewind(); it.copyPixelsFromBuffer(pixels) }
    }
    override fun close() {
        check(Thread.currentThread() === ownerThread)
        if (closed) return
        closed = true
        texture?.setOnFrameAvailableListener(null)
        ownedSurface?.release()
        ownedSurface = null
        texture?.release()
        texture = null
        callbacks?.quitSafely()
        callbacks = null
        if (context != EGL14.EGL_NO_CONTEXT && EGL14.eglGetCurrentContext() == context) {
            shaders.forEach(GLES20::glDeleteShader)
            if (program != 0) GLES20.glDeleteProgram(program)
            if (textureId != 0) GLES20.glDeleteTextures(1, intArrayOf(textureId), 0)
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
        }
        if (pbuffer != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(display, pbuffer)
        if (context != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(display, context)
        EGL14.eglReleaseThread()
        // Balanced against this instance's eglInitialize; other threads keep their own refs.
        if (initialized) EGL14.eglTerminate(display)
    }
}
