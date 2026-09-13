// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.libvlc

import android.view.Surface
import java.lang.ref.WeakReference

/** UI-owned Surfaces are detached synchronously before destruction; the backend never releases them. */
object VlcSurfaceOutput {
    private var reference = WeakReference<LibVlcPlayerBackend>(null)
    internal var owner: LibVlcPlayerBackend?
        get() = reference.get()
        set(value) { reference = WeakReference(value) }
    fun beforeDestroy(surface: Surface) { owner?.beforeSurfaceDestroyed(surface) }
}
