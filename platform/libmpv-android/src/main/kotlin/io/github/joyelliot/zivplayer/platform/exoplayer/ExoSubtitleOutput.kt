// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import android.view.View
import android.view.TextureView
import android.graphics.SurfaceTexture
import android.widget.FrameLayout
import androidx.media3.common.text.CueGroup
import androidx.media3.ui.SubtitleView
import io.github.peerless2012.ass.media.AssHandler
import io.github.peerless2012.ass.media.widget.AssSubtitleTextureView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/** Same-process, view-only bridge. The service continues to own the player and libass. */
internal object ExoSubtitleOutput {
    data class State(val owner: Any? = null, val handler: AssHandler? = null,
        val cues: CueGroup = CueGroup.EMPTY_TIME_ZERO)
    val state = MutableStateFlow(State())

    fun clear(owner: Any) {
        if (state.value.owner === owner) state.value = State()
    }
}

/** Attaches an independent GL subtitle layer to the same video rectangle. */
fun createExoSubtitleView(context: Context): View = object : FrameLayout(context) {
    private val ordinary = SubtitleView(context)
    private var boundHandler: AssHandler? = null
    private var assView: View? = null
    private var scope: CoroutineScope? = null

    init { addView(ordinary, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)) }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        scope = CoroutineScope(Dispatchers.Main.immediate + Job()).also { active ->
            active.launch {
                ExoSubtitleOutput.state.collect { state ->
                    if (boundHandler !== state.handler) {
                        boundHandler?.videoTimeCallback = null
                        assView?.let(::removeView)
                        assView = null
                        boundHandler = state.handler
                        state.handler?.let { handler ->
                            assView = AssSubtitleTextureView(context, handler).also { overlay ->
                                // The upstream widget drops requests before its GL thread exists.
                                // Replay the current time once the actual texture is ready, including
                                // paused playback, rotation and view recreation.
                                val delegate = checkNotNull(overlay.surfaceTextureListener)
                                overlay.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                                    override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) {
                                        handler.onSurfaceSizeChanged(width, height)
                                        delegate.onSurfaceTextureAvailable(surface, width, height)
                                        overlay.requestRender(handler.videoTime)
                                    }
                                    override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) {
                                        handler.onSurfaceSizeChanged(width, height)
                                        delegate.onSurfaceTextureSizeChanged(surface, width, height)
                                        overlay.requestRender(handler.videoTime)
                                    }
                                    override fun onSurfaceTextureDestroyed(surface: SurfaceTexture) = delegate.onSurfaceTextureDestroyed(surface)
                                    override fun onSurfaceTextureUpdated(surface: SurfaceTexture) = delegate.onSurfaceTextureUpdated(surface)
                                }
                                handler.videoTimeCallback = overlay::requestRender
                                addView(overlay, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
                            }
                        }
                    }
                    ordinary.setCues(state.cues.cues)
                }
            }
        }
    }

    override fun onDetachedFromWindow() {
        scope?.cancel()
        scope = null
        boundHandler?.videoTimeCallback = null
        assView?.let(::removeView)
        assView = null
        boundHandler = null
        super.onDetachedFromWindow()
    }
}
