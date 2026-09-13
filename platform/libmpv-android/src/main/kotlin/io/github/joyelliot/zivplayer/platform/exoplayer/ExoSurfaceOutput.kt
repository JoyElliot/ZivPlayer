// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.exoplayer

import android.view.Surface
import java.lang.ref.WeakReference

/** Same-process destruction fence; service leases still own attachment and replacement. */
object ExoSurfaceOutput {
    private var ownerReference: WeakReference<ExoPlayerBackend>? = null
    internal var owner: ExoPlayerBackend?
        get() = ownerReference?.get()
        set(value) { ownerReference = value?.let(::WeakReference) }
    fun beforeDestroy(surface: Surface) { owner?.detachSurfaceBeforeDestroy(surface) }
}
