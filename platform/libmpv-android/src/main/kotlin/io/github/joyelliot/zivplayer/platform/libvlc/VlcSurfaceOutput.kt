// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.libvlc

import android.view.Surface
import android.view.SurfaceView
import java.lang.ref.WeakReference

/** UI-owned Surfaces are detached synchronously before destruction; the backend never releases them. */
object VlcSurfaceOutput {
    internal data class Binding(
        val video: Surface,
        val subtitles: SurfaceView,
        val onLayout: (Int, Int, Int, Int, Int, Int) -> Unit,
    )
    private var binding: Binding? = null
    private var reference = WeakReference<LibVlcPlayerBackend>(null)
    internal var owner: LibVlcPlayerBackend?
        get() = reference.get()
        set(value) { reference = WeakReference(value) }
    fun register(video: Surface, subtitles: SurfaceView, onLayout: (Int, Int, Int, Int, Int, Int) -> Unit) {
        binding = Binding(video, subtitles, onLayout)
    }
    internal fun bindingFor(video: Surface) = binding?.takeIf { it.video === video && it.subtitles.holder.surface.isValid }
    fun beforeDestroy(surface: Surface) {
        owner?.beforeSurfaceDestroyed(surface)
        if (binding?.video === surface) binding = null
    }
}
