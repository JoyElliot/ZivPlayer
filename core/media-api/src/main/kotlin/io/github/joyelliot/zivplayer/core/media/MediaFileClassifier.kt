// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.media

import java.util.Locale

object MediaFileClassifier {
    private val audioExtensions = setOf("mp3", "m4a", "aac", "flac", "wav", "wave", "ogg", "opus", "wma",
        "ape", "aif", "aiff", "alac", "mka", "ac3", "eac3", "dts", "mp2", "amr", "au", "caf")
    private val extensions = setOf("mp4", "mkv", "webm", "mov", "avi", "m4v", "3gp", "3g2", "flv",
        "wmv", "asf", "ts", "mts", "m2ts", "mpg", "mpeg", "m2v", "vob", "ogv", "rm", "rmvb",
        "mp3", "m4a", "aac", "flac", "wav", "wave", "ogg", "opus", "wma", "ape", "aif", "aiff",
        "alac", "mka", "ac3", "eac3", "dts", "mp2", "amr", "au", "caf")
    fun isMedia(name: String, mimeType: String?): Boolean {
        val type = mimeType?.lowercase(Locale.ROOT).orEmpty()
        return type.startsWith("video/") || type.startsWith("audio/") ||
            name.substringAfterLast('.', "").lowercase(Locale.ROOT) in extensions
    }
    fun isAudio(name: String, mimeType: String?): Boolean {
        val type = mimeType?.lowercase(Locale.ROOT).orEmpty()
        if (type.startsWith("audio/")) return true
        if (type.startsWith("video/")) return false
        return name.substringAfterLast('.', "").lowercase(Locale.ROOT) in audioExtensions
    }
}
