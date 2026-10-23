// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.listSaver

/** Apply the video's default once per opening/item, without overriding a manual turn. */
internal class PlayerOpeningOrientation {
    var epoch by mutableIntStateOf(0)
        private set
    private var expectedSource: String? = null
    private var expectedMedia: String? = null
    private var handledIdentity: String? = null
    private var previousIdentity: String? = null
    private var manualWhileOpening = false

    fun open(source: String?, mediaId: String?, waitForReplacementOf: String? = null) {
        expectedSource = source
        expectedMedia = mediaId
        handledIdentity = null
        previousIdentity = waitForReplacementOf
        manualWhileOpening = false
        epoch++
    }

    fun manual(identity: String?) {
        if (expectedSource != null || expectedMedia != null || identity == null) manualWhileOpening = true
        else handledIdentity = identity
    }

    fun resolve(identity: String?, source: String?, mediaId: String?, aspect: Float?, entryLandscape: Boolean): Boolean? {
        if (identity == null) return null
        if (identity == previousIdentity) return null
        if (expectedSource != null && source != expectedSource || expectedMedia != null && mediaId != expectedMedia) return null
        expectedSource = null
        expectedMedia = null
        previousIdentity = null
        if (handledIdentity == identity) return null
        if (manualWhileOpening) { handledIdentity = identity; manualWhileOpening = false; return null }
        if (aspect == null || !aspect.isFinite() || aspect <= 0f) return null
        handledIdentity = identity
        // A square video keeps the entry orientation; audio/unknown geometry never requests a turn.
        return if (aspect == 1f) false else (aspect > 1f) != entryLandscape
    }

    companion object {
        val Saver = listSaver<PlayerOpeningOrientation, Any>(save = {
            listOf(it.epoch, it.expectedSource.orEmpty(), it.expectedMedia.orEmpty(),
                it.handledIdentity.orEmpty(), it.manualWhileOpening, it.previousIdentity.orEmpty())
        }, restore = { saved -> PlayerOpeningOrientation().apply {
            epoch = saved[0] as Int
            expectedSource = (saved[1] as String).ifEmpty { null }
            expectedMedia = (saved[2] as String).ifEmpty { null }
            handledIdentity = (saved[3] as String).ifEmpty { null }
            manualWhileOpening = saved[4] as Boolean
            previousIdentity = (saved[5] as String).ifEmpty { null }
        } })
    }
}
