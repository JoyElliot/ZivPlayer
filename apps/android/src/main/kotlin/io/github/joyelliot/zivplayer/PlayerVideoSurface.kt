// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.Context
import android.graphics.Bitmap
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.PixelCopy
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.widget.FrameLayout
import android.widget.ImageView
import android.view.View
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import io.github.joyelliot.zivplayer.platform.playback.VideoSurfaceRequestContract
import io.github.joyelliot.zivplayer.platform.playback.VideoSurfaceRequests
import io.github.joyelliot.zivplayer.platform.playback.PlaybackExperiment
import io.github.joyelliot.zivplayer.core.model.VideoFit
import kotlin.math.max
import kotlin.math.roundToInt

@Composable
internal fun PlayerVideoSurface(
    player: MediaController?,
    canRenderVideo: Boolean,
    modifier: Modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
    videoAspectRatio: Float = 16f / 9f,
    videoFit: VideoFit = VideoFit.FIT,
    previewFrame: SeekPreviewFrame? = null,
    onPreviewPresented: (Long) -> Unit = {},
) {
    val context = LocalContext.current
    val viewport = remember(context) { VideoViewport(context) }
    val videoView = viewport.surfaceView
    val sourceDimensions = remember(player) { mutableStateOf(0 to 0) }
    DisposableEffect(player) {
        fun updateSourceDimensions() {
            val group = player?.currentTracks?.groups?.firstOrNull {
                it.type == androidx.media3.common.C.TRACK_TYPE_VIDEO && it.isSelected
            }
            val format = group?.let { (0 until it.length).firstOrNull(it::isTrackSelected)?.let(it::getTrackFormat) }
            sourceDimensions.value = (format?.width ?: 0) to (format?.height ?: 0)
        }
        val listener = object : Player.Listener {
            override fun onTracksChanged(tracks: androidx.media3.common.Tracks) = updateSourceDimensions()
        }
        player?.addListener(listener)
        updateSourceDimensions()
        onDispose { player?.removeListener(listener) }
    }
    AndroidView(factory = { viewport }, modifier = modifier.background(Color.Black), update = {
        it.configure(videoAspectRatio, videoFit, sourceDimensions.value.first, sourceDimensions.value.second)
        it.showPreview(previewFrame, onPreviewPresented)
    })

    DisposableEffect(player, videoView, canRenderVideo) {
        if (player == null || !canRenderVideo || !player.canSetVideoSurface()) {
            onDispose { }
        } else {
            var attachedSurface: Surface? = null
            var surfaceToken = 0L
            var resizeGeneration = 0L
            val redrawCompletions = mutableSetOf<Runnable>()
            val redrawHandler = Handler(Looper.getMainLooper())
            fun resizeSurface() {
                val width = viewport.renderWidth
                val height = viewport.renderHeight
                if (width <= 0 || height <= 0 || !VideoSurfaceRequests.isCurrent(surfaceToken) || !player.isConnected) return
                val command = SessionCommand(VideoSurfaceRequestContract.ACTION_RESIZE, Bundle.EMPTY)
                if (!player.isSessionCommandAvailable(command)) return
                val future = player.sendCustomCommand(command, Bundle().apply {
                    putLong(VideoSurfaceRequestContract.TOKEN, surfaceToken)
                    putInt(VideoSurfaceRequestContract.WIDTH, width)
                    putInt(VideoSurfaceRequestContract.HEIGHT, height)
                })
                future.addListener({
                    val result = runCatching { future.get() }
                    if (result.getOrNull()?.resultCode != SessionResult.RESULT_SUCCESS) {
                        Log.w("ZivVideoSurface", "Surface resize was not accepted.", result.exceptionOrNull())
                    }
                }, java.util.concurrent.Executor { it.run() })
            }
            fun detachSurface() {
                viewport.onVideoSurfaceDetached()
                attachedSurface?.let(PlaybackExperiment::beforeSurfaceDestroyed)
                if (VideoSurfaceRequests.release(surfaceToken) && player.canSetVideoSurface()) {
                    attachedSurface?.let(player::clearVideoSurface)
                }
                // SurfaceHolder owns this Surface, including its final release.
                attachedSurface = null
                surfaceToken = 0L
                ++resizeGeneration
                redrawCompletions.toList().forEach(Runnable::run)
            }
            fun redrawAfterResize(onRedrawRequested: () -> Unit = {}) {
                val generation = ++resizeGeneration
                resizeSurface()
                // A paused producer also needs redraws after EGL adopts the new
                // buffer geometry. These callbacks do not claim frame presentation.
                videoView.postOnAnimation {
                    if (generation == resizeGeneration && attachedSurface != null) {
                        resizeSurface()
                        videoView.postOnAnimation {
                            if (generation == resizeGeneration && attachedSurface != null) resizeSurface()
                            onRedrawRequested()
                        }
                    } else onRedrawRequested()
                }
            }
            fun attachSurface(surface: Surface) {
                if (attachedSurface != null || !surface.isValid || !player.canSetVideoSurface()) return
                attachedSurface = surface
                surfaceToken = VideoSurfaceRequests.claim()
                viewport.onVideoSurfaceAttached()
                player.setVideoSurface(surface)
                redrawAfterResize()
            }
            viewport.onRenderSizeChanged = {
                videoView.holder.setFixedSize(viewport.renderWidth, viewport.renderHeight)
                redrawAfterResize()
            }
            val callback = object : SurfaceHolder.Callback2 {
                override fun surfaceCreated(holder: SurfaceHolder) {
                    attachSurface(holder.surface)
                }
                override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
                    redrawAfterResize()
                    viewport.onVideoSurfaceReady()
                }
                override fun surfaceDestroyed(holder: SurfaceHolder) {
                    detachSurface()
                }
                override fun surfaceRedrawNeeded(holder: SurfaceHolder) {
                    redrawAfterResize()
                }
                override fun surfaceRedrawNeededAsync(holder: SurfaceHolder, drawingFinished: Runnable) {
                    var finished = false
                    lateinit var finish: Runnable
                    finish = Runnable {
                        if (!finished) {
                            finished = true
                            redrawHandler.removeCallbacks(finish)
                            redrawCompletions.remove(finish)
                            drawingFinished.run()
                        }
                    }
                    redrawCompletions.add(finish)
                    // Detached views may never run another animation callback.
                    redrawHandler.postDelayed(finish, 1_000)
                    fun probeBuffer() {
                        if (finished) return
                        if (!holder.surface.isValid || attachedSurface == null) { finish.run(); return }
                        val bitmap = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888)
                        try {
                            PixelCopy.request(videoView, bitmap, { result ->
                                bitmap.recycle()
                                if (!finished) {
                                    if (result == PixelCopy.SUCCESS) finish.run()
                                    else redrawHandler.postDelayed({ probeBuffer() }, 16)
                                }
                            }, redrawHandler)
                        } catch (_: IllegalArgumentException) { bitmap.recycle(); finish.run() }
                    }
                    // API 26+ must not complete its synchronous default callback
                    // while all native redraw requests are still queued.
                    redrawAfterResize { probeBuffer() }
                }
            }
            videoView.holder.setFixedSize(viewport.renderWidth, viewport.renderHeight)
            videoView.holder.addCallback(callback)
            attachSurface(videoView.holder.surface)
            onDispose {
                viewport.onRenderSizeChanged = null
                videoView.holder.removeCallback(callback)
                detachSurface()
            }
        }
    }
}

/** Keeps the renderer's buffer geometry stable while the window rotates. */
private class VideoViewport(context: Context) : FrameLayout(context) {
    val surfaceView = SurfaceView(context)
    private val subtitles = if (PlaybackExperiment.usesExoPlayer) PlaybackExperiment.subtitleView(context) else null
    private val previewView = ImageView(context).apply {
        tag = "seek-preview-frame"
        scaleType = ImageView.ScaleType.FIT_XY
        visibility = View.GONE
    }
    private var preview: SeekPreviewFrame? = null
    private var settlingToken: Long? = null
    private var surfaceGeneration = 0L
    private var surfaceAttached = false
    private var previewPresented: (Long) -> Unit = {}
    private var videoAspectRatio = 16f / 9f
    private var videoFit = VideoFit.FIT
    private val renderLongEdge = max(resources.displayMetrics.widthPixels, resources.displayMetrics.heightPixels)
        .coerceIn(256, 4096).let { it - it % 2 }
    var renderWidth = renderLongEdge
        private set
    var renderHeight = (renderLongEdge / videoAspectRatio).roundToInt().let { it - it % 2 }
        private set
    var onRenderSizeChanged: (() -> Unit)? = null

    init {
        setBackgroundColor(android.graphics.Color.BLACK)
        clipChildren = true
        // Default SurfaceView ordering leaves app chrome and this preview above
        // the video. Setting z-order-on-top would hide controls and subtitles UI.
        addView(surfaceView)
        subtitles?.let(::addView)
        addView(previewView)
    }

    fun showPreview(frame: SeekPreviewFrame?, onPresented: (Long) -> Unit) {
        preview = frame
        previewPresented = onPresented
        if (frame == null) {
            previewView.setImageDrawable(null)
            previewView.visibility = View.GONE
            settlingToken = null
            return
        }
        previewView.setImageBitmap(frame.bitmap)
        previewView.visibility = View.VISIBLE
        settlePreview()
    }

    fun onVideoSurfaceAttached() {
        onVideoSurfaceDetached()
        surfaceAttached = true
        settlePreview()
    }

    fun onVideoSurfaceDetached() {
        ++surfaceGeneration
        surfaceAttached = false
        settlingToken = null
    }

    fun onVideoSurfaceReady() = settlePreview()

    private fun settlePreview() {
        val frame = preview ?: return
        if (frame.commitReady && surfaceAttached && surfaceView.holder.surface.isValid && settlingToken != frame.token) {
            settlingToken = frame.token
            val generation = surfaceGeneration
            fun isCurrent() = generation == surfaceGeneration && surfaceAttached && surfaceView.holder.surface.isValid &&
                preview?.token == frame.token && preview?.commitReady == true
            val deadline = SystemClock.uptimeMillis() + 1_000
            fun awaitSurfaceBuffer() {
                if (!isCurrent()) return
                val probe = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888)
                fun finish(result: Int) {
                    probe.recycle()
                    if (!isCurrent()) return
                    if (result == PixelCopy.SUCCESS || SystemClock.uptimeMillis() >= deadline) {
                        // Native seek completion is the frame/position fence. A
                        // successful copy only proves this Surface has a buffer;
                        // it does not identify that buffer's presentation time.
                        if (result != PixelCopy.SUCCESS) Log.w("ZivVideoSurface", "Surface buffer probe timed out: $result")
                        previewPresented(frame.token)
                    } else surfaceView.postOnAnimation { awaitSurfaceBuffer() }
                }
                try {
                    PixelCopy.request(surfaceView, probe, { finish(it) }, Handler(Looper.getMainLooper()))
                } catch (_: IllegalArgumentException) { finish(PixelCopy.ERROR_SOURCE_INVALID) }
            }
            // Cross the compositor boundary only after the correlated native
            // completion; a recreated Surface must additionally have a buffer.
            surfaceView.postOnAnimation {
                if (isCurrent()) surfaceView.postOnAnimation { awaitSurfaceBuffer() }
            }
        }
    }

    fun configure(aspectRatio: Float, fit: VideoFit, sourceWidth: Int = 0, sourceHeight: Int = 0) {
        val ratio = aspectRatio.takeIf { it.isFinite() && it > 0f } ?: (16f / 9f)
        val presentationChanged = videoAspectRatio != ratio || videoFit != fit
        videoAspectRatio = ratio
        videoFit = fit
        val testSize = PlaybackExperiment.instrumentationSurfaceSize
        val useSourceSize = PlaybackExperiment.usesExoPlayer && sourceWidth in 1..16384 && sourceHeight in 1..16384
        val newWidth = (testSize?.first ?: if (useSourceSize) sourceWidth else if (ratio >= 1f) renderLongEdge else (renderLongEdge * ratio).roundToInt())
            .coerceAtLeast(2).let { it - it % 2 }
        val newHeight = (testSize?.second ?: if (useSourceSize) sourceHeight else if (ratio >= 1f) (renderLongEdge / ratio).roundToInt() else renderLongEdge)
            .coerceAtLeast(2).let { it - it % 2 }
        if (!presentationChanged && renderWidth == newWidth && renderHeight == newHeight) return
        if (renderWidth != newWidth || renderHeight != newHeight) {
            renderWidth = newWidth
            renderHeight = newHeight
            onRenderSizeChanged?.invoke()
        }
        requestLayout()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val height = MeasureSpec.getSize(heightMeasureSpec)
        setMeasuredDimension(width, height)
        val viewRatio = if (height > 0) width.toFloat() / height else videoAspectRatio
        val matchWidth = when (videoFit) {
            VideoFit.FIT -> viewRatio <= videoAspectRatio
            VideoFit.FILL -> viewRatio >= videoAspectRatio
            VideoFit.STRETCH -> true
        }
        val childWidth = if (videoFit == VideoFit.STRETCH || matchWidth) width else (height * videoAspectRatio).roundToInt()
        val childHeight = if (videoFit == VideoFit.STRETCH || !matchWidth) height else (width / videoAspectRatio).roundToInt()
        surfaceView.measure(MeasureSpec.makeMeasureSpec(childWidth.coerceAtLeast(1), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(childHeight.coerceAtLeast(1), MeasureSpec.EXACTLY))
        previewView.measure(MeasureSpec.makeMeasureSpec(childWidth.coerceAtLeast(1), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(childHeight.coerceAtLeast(1), MeasureSpec.EXACTLY))
        subtitles?.measure(MeasureSpec.makeMeasureSpec(childWidth.coerceAtLeast(1), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(childHeight.coerceAtLeast(1), MeasureSpec.EXACTLY))
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        val childLeft = (width - surfaceView.measuredWidth) / 2
        val childTop = (height - surfaceView.measuredHeight) / 2
        surfaceView.layout(childLeft, childTop, childLeft + surfaceView.measuredWidth, childTop + surfaceView.measuredHeight)
        previewView.layout(childLeft, childTop, childLeft + surfaceView.measuredWidth, childTop + surfaceView.measuredHeight)
        subtitles?.layout(childLeft, childTop, childLeft + surfaceView.measuredWidth, childTop + surfaceView.measuredHeight)
    }
}

private fun MediaController.canSetVideoSurface(): Boolean =
    isConnected && isCommandAvailable(Player.COMMAND_SET_VIDEO_SURFACE)
