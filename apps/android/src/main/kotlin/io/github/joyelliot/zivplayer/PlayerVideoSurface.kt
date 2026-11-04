// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer

import android.content.Context
import android.graphics.SurfaceTexture
import android.graphics.Typeface
import android.os.Build
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.TextureView
import android.view.View
import android.widget.FrameLayout
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.Player
import androidx.media3.common.text.Cue
import androidx.media3.common.text.CueGroup
import androidx.media3.session.MediaController
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.SubtitleView
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.SubtitleColor
import io.github.joyelliot.zivplayer.core.model.VideoFit
import io.github.joyelliot.zivplayer.platform.playback.VideoSurfaceRequests
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.max
import kotlin.math.roundToInt

@Composable
internal fun PlayerVideoSurface(
    player: MediaController?,
    canRenderVideo: Boolean,
    modifier: Modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
    videoAspectRatio: Float = 16f / 9f,
    videoFit: VideoFit = VideoFit.FIT,
    useSurfaceView: Boolean = false,
    options: PlaybackOptions = PlaybackOptions(),
) {
    val context = LocalContext.current
    val viewport = remember(context) { VideoViewport(context) }
    LaunchedEffect(viewport) {
        VideoSurfaceRequests.renderedToken.collect { viewport.onFrameRendered(it) }
    }
    val typeface by produceState<Typeface?>(null, context.applicationContext, options.subtitleFont) {
        value = withContext(Dispatchers.IO) {
            val app = context.applicationContext as? ZivPlayerApplication
            options.subtitleFont?.let { id -> runCatching {
                app?.mediaRepositories?.resources?.resolve(id, PlaybackResourceKind.FONT)?.let(Typeface::createFromFile)
            }.getOrNull() }
        }
    }
    AndroidView(factory = { viewport }, modifier = modifier.background(Color.Black), update = {
        it.configure(videoAspectRatio, videoFit, useSurfaceView, options, typeface)
    })
    DisposableEffect(player, viewport, canRenderVideo) {
        viewport.bind(player?.takeIf { canRenderVideo && it.canSetVideoSurface() })
        onDispose { viewport.bind(null) }
    }
}

/** 两个显示目标共享一个播放器；只有当前目标持有进程级输出令牌。 */
internal class VideoViewport(context: Context) : FrameLayout(context) {
    val textureView = TextureView(context)
    val surfaceView = SurfaceView(context)
    private val subtitleView = SubtitleView(context)
    private var controller: MediaController? = null
    private var textureSurface: Surface? = null
    private var attachedSurface: Surface? = null
    private var surfaceToken = 0L
    private var directRequested = false
    private var holderReady = false
    internal var directOutputActive = false
        private set
    private var videoAspectRatio = 16f / 9f
    private var videoFit = VideoFit.FIT
    private var options = PlaybackOptions()
    private var cues = emptyList<Cue>()
    private var font: Typeface? = null
    private val renderLongEdge = max(resources.displayMetrics.widthPixels, resources.displayMetrics.heightPixels)
        .coerceIn(256, 4096).let { it - it % 2 }
    private var renderWidth = renderLongEdge
    private var renderHeight = (renderLongEdge / videoAspectRatio).roundToInt().let { it - it % 2 }

    private val listener = object : Player.Listener {
        override fun onCues(cueGroup: CueGroup) { cues = cueGroup.cues; updateSubtitles() }
    }

    init {
        setBackgroundColor(android.graphics.Color.BLACK)
        clipChildren = true
        surfaceView.visibility = View.INVISIBLE
        if (Build.VERSION.SDK_INT >= 34) surfaceView.setSurfaceLifecycle(SurfaceView.SURFACE_LIFECYCLE_FOLLOWS_ATTACHMENT)
        addView(surfaceView)
        addView(textureView)
        addView(subtitleView)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) { holderReady = true; selectOutput() }
            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) { selectOutput() }
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                holderReady = false
                if (directOutputActive) detachOutput()
                selectOutput()
            }
        })
        textureView.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
                texture.setDefaultBufferSize(renderWidth, renderHeight)
                textureSurface = Surface(texture)
                selectOutput()
            }
            override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {
                texture.setDefaultBufferSize(renderWidth, renderHeight)
            }
            override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
                if (attachedSurface === textureSurface) detachOutput()
                textureSurface?.release()
                textureSurface = null
                return true
            }
            override fun onSurfaceTextureUpdated(texture: SurfaceTexture) = Unit
        }
    }

    fun bind(player: MediaController?) {
        if (controller === player) return
        controller?.removeListener(listener)
        detachOutput()
        controller = player
        cues = if (player?.isConnected == true && player.isCommandAvailable(Player.COMMAND_GET_TEXT)) player.currentCues.cues else emptyList()
        player?.addListener(listener)
        updateSubtitles()
        selectOutput()
    }

    private fun detachOutput() {
        val previous = attachedSurface
        if (VideoSurfaceRequests.release(surfaceToken) && controller?.canSetVideoSurface() == true && previous != null) {
            controller?.clearVideoSurface(previous)
        }
        attachedSurface = null
        directOutputActive = false
        surfaceToken = 0L
    }

    private fun selectOutput() {
        val player = controller ?: return
        if (!player.canSetVideoSurface()) return
        val direct = directRequested && holderReady && surfaceView.visibility == View.VISIBLE && surfaceView.holder.surface.isValid
        val target = if (direct) surfaceView.holder.surface else textureSurface?.takeIf { it.isValid }
        if (target != null && target !== attachedSurface) {
            // 设置新目标时由后端替换旧目标，旧视图后续的销毁不再有权清除输出。
            surfaceToken = VideoSurfaceRequests.claim()
            attachedSurface = target
            directOutputActive = direct
            player.setVideoSurface(target)
        }
        // 保留纹理中的上一帧，直到新 Surface 确实收到首帧；不以 READY 冒充出帧。
        textureView.alpha = if (directOutputActive && VideoSurfaceRequests.renderedToken.value == surfaceToken) 0f else 1f
    }

    fun onFrameRendered(token: Long) {
        if (directOutputActive && token == surfaceToken && VideoSurfaceRequests.isCurrent(token)) textureView.alpha = 0f
    }

    fun configure(aspectRatio: Float, fit: VideoFit, direct: Boolean, options: PlaybackOptions, font: Typeface?) {
        val ratio = aspectRatio.takeIf { it.isFinite() && it > 0f } ?: (16f / 9f)
        if (videoAspectRatio != ratio || videoFit != fit) {
            videoAspectRatio = ratio; videoFit = fit
            renderWidth = (if (ratio >= 1f) renderLongEdge else (renderLongEdge * ratio).roundToInt()).coerceAtLeast(2).let { it - it % 2 }
            renderHeight = (if (ratio >= 1f) (renderLongEdge / ratio).roundToInt() else renderLongEdge).coerceAtLeast(2).let { it - it % 2 }
            textureView.surfaceTexture?.setDefaultBufferSize(renderWidth, renderHeight)
            requestLayout()
        }
        if (this.options != options || this.font !== font) {
            this.options = options; this.font = font
            updateSubtitles()
        }
        if (directRequested != direct) {
            directRequested = direct
            // 回到纹理输出后才隐藏 SurfaceView，避免旧 holder 销毁清掉新目标。
            if (!direct) selectOutput()
            surfaceView.visibility = if (direct) View.VISIBLE else View.INVISIBLE
        }
        selectOutput()
    }

    private fun updateSubtitles() {
        subtitleView.setApplyEmbeddedStyles(!options.overrideStyledSubtitles)
        subtitleView.setApplyEmbeddedFontSizes(!options.overrideStyledSubtitles && options.subtitleScalePercent == 100)
        subtitleView.setFractionalTextSize(SubtitleView.DEFAULT_TEXT_SIZE_FRACTION * options.subtitleScalePercent / 100f)
        val color = when (options.subtitleColor) {
            SubtitleColor.WHITE -> android.graphics.Color.WHITE
            SubtitleColor.YELLOW -> android.graphics.Color.YELLOW
            SubtitleColor.GREEN -> 0xff66ff66.toInt()
        }
        subtitleView.setStyle(CaptionStyleCompat(color, android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT,
            if (options.subtitleBorderSize == 0) CaptionStyleCompat.EDGE_TYPE_NONE else CaptionStyleCompat.EDGE_TYPE_OUTLINE,
            android.graphics.Color.BLACK, font))
        subtitleView.setCues(cues.map { cue ->
            if (options.subtitlePositionPercent == 100 && !options.overrideStyledSubtitles) cue
            else cue.buildUpon().setLine((options.subtitlePositionPercent / 100f * .92f).coerceAtLeast(.08f), Cue.LINE_TYPE_FRACTION)
                .setLineAnchor(Cue.ANCHOR_TYPE_END).build()
        })
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
        for (child in listOf(surfaceView, textureView, subtitleView)) child.measure(
            MeasureSpec.makeMeasureSpec(childWidth.coerceAtLeast(1), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(childHeight.coerceAtLeast(1), MeasureSpec.EXACTLY))
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        val childLeft = (width - textureView.measuredWidth) / 2
        val childTop = (height - textureView.measuredHeight) / 2
        for (child in listOf(surfaceView, textureView, subtitleView)) child.layout(childLeft, childTop,
            childLeft + textureView.measuredWidth, childTop + textureView.measuredHeight)
    }
}

private fun MediaController.canSetVideoSurface(): Boolean =
    isConnected && isCommandAvailable(Player.COMMAND_SET_VIDEO_SURFACE)
