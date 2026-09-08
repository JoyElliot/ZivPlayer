// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.content.Context
import android.graphics.SurfaceTexture
import android.os.Bundle
import android.util.Log
import android.view.Surface
import android.view.TextureView
import android.widget.FrameLayout
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
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
) {
    val context = LocalContext.current
    val viewport = remember(context) { VideoViewport(context) }
    val videoView = viewport.textureView
    AndroidView(factory = { viewport }, modifier = modifier.background(Color.Black), update = {
        it.configure(videoAspectRatio, videoFit)
    })

    DisposableEffect(player, videoView, canRenderVideo) {
        if (player == null || !canRenderVideo || !player.canSetVideoSurface()) {
            onDispose { }
        } else {
            var attachedSurface: Surface? = null
            var surfaceToken = 0L
            var resizeRedrawsRemaining = 0
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
                if (VideoSurfaceRequests.release(surfaceToken) && player.canSetVideoSurface()) {
                    attachedSurface?.let(player::clearVideoSurface)
                }
                attachedSurface?.release()
                attachedSurface = null
                resizeRedrawsRemaining = 0
            }
            fun attachSurface(texture: SurfaceTexture) {
                if (attachedSurface != null || !player.canSetVideoSurface()) return
                texture.setDefaultBufferSize(viewport.renderWidth, viewport.renderHeight)
                val surface = Surface(texture)
                attachedSurface = surface
                surfaceToken = VideoSurfaceRequests.claim()
                resizeRedrawsRemaining = 2
                player.setVideoSurface(surface)
                resizeSurface()
            }
            viewport.onRenderSizeChanged = {
                videoView.surfaceTexture?.setDefaultBufferSize(viewport.renderWidth, viewport.renderHeight)
                resizeRedrawsRemaining = 2
                resizeSurface()
            }
            videoView.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
                    attachSurface(texture)
                }
                override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {
                    // The producer resolution is independent of the displayed View
                    // size. Rotation only scales a frame with the same aspect ratio.
                    texture.setDefaultBufferSize(viewport.renderWidth, viewport.renderHeight)
                    resizeSurface()
                }
                override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
                    detachSurface()
                    return true
                }
                override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {
                    // EGL can adopt the new buffer geometry at swap time. Drain
                    // the old-size buffers with redraws paced by actual presents,
                    // including while playback is paused; never seek or resume.
                    if (resizeRedrawsRemaining > 0) {
                        resizeRedrawsRemaining--
                        resizeSurface()
                    }
                }
            }
            videoView.surfaceTexture?.takeIf { videoView.isAvailable }?.let {
                attachSurface(it)
            }
            onDispose {
                viewport.onRenderSizeChanged = null
                videoView.surfaceTextureListener = null
                detachSurface()
            }
        }
    }
}

/** Keeps the renderer's buffer geometry stable while the window rotates. */
private class VideoViewport(context: Context) : FrameLayout(context) {
    val textureView = TextureView(context)
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
        addView(textureView)
    }

    fun configure(aspectRatio: Float, fit: VideoFit) {
        val ratio = aspectRatio.takeIf { it.isFinite() && it > 0f } ?: (16f / 9f)
        if (videoAspectRatio == ratio && videoFit == fit) return
        videoAspectRatio = ratio
        videoFit = fit
        val newWidth = (if (ratio >= 1f) renderLongEdge else (renderLongEdge * ratio).roundToInt())
            .coerceAtLeast(2).let { it - it % 2 }
        val newHeight = (if (ratio >= 1f) (renderLongEdge / ratio).roundToInt() else renderLongEdge)
            .coerceAtLeast(2).let { it - it % 2 }
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
        textureView.measure(MeasureSpec.makeMeasureSpec(childWidth.coerceAtLeast(1), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(childHeight.coerceAtLeast(1), MeasureSpec.EXACTLY))
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        val childLeft = (width - textureView.measuredWidth) / 2
        val childTop = (height - textureView.measuredHeight) / 2
        textureView.layout(childLeft, childTop, childLeft + textureView.measuredWidth, childTop + textureView.measuredHeight)
    }
}

private fun MediaController.canSetVideoSurface(): Boolean =
    isConnected && isCommandAvailable(Player.COMMAND_SET_VIDEO_SURFACE)
