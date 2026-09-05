// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.util.AtomicFile
import java.io.File

/** Private configuration containing only Android font discovery and a writable cache. */
internal object MpvFontConfig {
    fun prepare(context: Context): File {
        val directory = File(context.filesDir, "mpv")
        val cache = File(context.cacheDir, "fontconfig")
        check(directory.isDirectory || directory.mkdirs()) { "Cannot create the mpv configuration directory." }
        check(cache.isDirectory || cache.mkdirs()) { "Cannot create the font cache directory." }
        val contents = """
            <?xml version="1.0"?>
            <!DOCTYPE fontconfig SYSTEM "fonts.dtd">
            <fontconfig>
              <dir>/system/fonts/</dir>
              <dir>/product/fonts/</dir>
              <cachedir>${xml(cache.absolutePath)}</cachedir>
              <alias><family>serif</family><prefer><family>Noto Serif</family></prefer></alias>
              <alias><family>sans-serif</family><prefer><family>Roboto</family><family>Noto Sans</family></prefer></alias>
              <alias><family>monospace</family><prefer><family>Droid Sans Mono</family></prefer></alias>
            </fontconfig>
        """.trimIndent() + "\n"
        val target = File(directory, "fonts.conf")
        if (!target.isFile || target.readText() != contents) {
            val atomic = AtomicFile(target)
            val output = atomic.startWrite()
            try {
                output.write(contents.toByteArray(Charsets.UTF_8))
                atomic.finishWrite(output)
            } catch (failure: Throwable) {
                atomic.failWrite(output)
                throw failure
            }
        }
        return directory
    }

    private fun xml(value: String): String = value.replace("&", "&amp;")
        .replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&apos;")
}
