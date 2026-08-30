// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.view.Surface

/**
 * Android-only render target boundary owned by the same adapter as libmpv.
 *
 * A lease prevents a late Surface lifecycle callback from detaching a newer
 * Surface. Closing the backend invalidates every outstanding lease.
 */
interface LibmpvSurfacePort {
    fun attachSurface(surface: Surface): LibmpvSurfaceLease

    fun detachSurface(lease: LibmpvSurfaceLease)
}

class LibmpvSurfaceLease internal constructor(
    internal val owner: Any,
    internal val token: Long,
)
