// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.ContentResolver
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.Closeable

internal class OpenMpvMediaSource(val locator: String, private val release: () -> Unit = {}) : Closeable {
    private var closed = false
    override fun close() { if (!closed) { release(); closed = true } }
}

internal fun interface MpvMediaSourceOpener {
    suspend fun open(locator: String): OpenMpvMediaSource
}

internal class AndroidMpvMediaSourceOpener(private val resolver: ContentResolver) : MpvMediaSourceOpener {
    override suspend fun open(locator: String): OpenMpvMediaSource {
        if (!locator.startsWith("content://", ignoreCase = true)) return OpenMpvMediaSource(locator)
        var opened: OpenMpvMediaSource? = null
        var descriptor: android.os.ParcelFileDescriptor? = null
        try {
            withContext(Dispatchers.IO) {
                val owned = resolver.openFileDescriptor(Uri.parse(locator), "r")
                    ?: error("The selected media is no longer readable.")
                descriptor = owned
                // libmpv borrows this FD. It must never close it or reopen a /proc alias.
                opened = OpenMpvMediaSource("fd://${owned.fd}", owned::close)
            }
            return checkNotNull(opened)
        } catch (failure: Throwable) {
            runCatching { if (opened != null) opened.close() else descriptor?.close() }
                .exceptionOrNull()?.let(failure::addSuppressed)
            throw failure
        }
    }
}
