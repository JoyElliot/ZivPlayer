// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.listSaver

/** Keep the current item's physical orientation through mini-player round trips. */
internal class PlayerOpeningOrientation {
    var epoch by mutableIntStateOf(0)
        private set
    private var expectedSource: String? = null
    private var expectedMedia: String? = null
    private var handledIdentity: String? = null
    private var previousIdentity: String? = null
    private var selectedIdentity: String? = null
    private var preferredLandscape: Boolean? = null
    private var manualWhileOpening: Boolean? = null

    fun open(source: String?, mediaId: String?, waitForReplacementOf: String? = null) {
        expectedSource = source
        expectedMedia = mediaId
        handledIdentity = null
        previousIdentity = waitForReplacementOf
        manualWhileOpening = null
        epoch++
    }

    fun manual(identity: String?, landscape: Boolean) {
        if (expectedSource != null || expectedMedia != null || identity == null) manualWhileOpening = landscape
        else {
            selectedIdentity = identity
            preferredLandscape = landscape
            handledIdentity = identity
        }
    }

    fun resolve(identity: String?, source: String?, mediaId: String?, aspect: Float?, entryLandscape: Boolean,
        readyWithoutGeometry: Boolean = false): Boolean? {
        if (identity == null) return null
        if (identity == previousIdentity) return null
        if (expectedSource != null && source != expectedSource || expectedMedia != null && mediaId != expectedMedia) return null
        expectedSource = null
        expectedMedia = null
        previousIdentity = null
        if (selectedIdentity != identity) {
            selectedIdentity = identity
            preferredLandscape = null
        }
        manualWhileOpening?.let { preferredLandscape = it; manualWhileOpening = null }
        if (handledIdentity == identity) return null
        if (preferredLandscape == null) {
            preferredLandscape = when {
                aspect != null && aspect.isFinite() && aspect > 0f -> if (aspect == 1f) entryLandscape else aspect > 1f
                readyWithoutGeometry -> entryLandscape
                else -> return null
            }
        }
        handledIdentity = identity
        return preferredLandscape != entryLandscape
    }

    companion object {
        val Saver = listSaver<PlayerOpeningOrientation, Any>(save = {
            listOf(it.epoch, it.expectedSource.orEmpty(), it.expectedMedia.orEmpty(),
                it.handledIdentity.orEmpty(), it.manualWhileOpening.encode(), it.previousIdentity.orEmpty(),
                it.selectedIdentity.orEmpty(), it.preferredLandscape.encode())
        }, restore = { saved -> PlayerOpeningOrientation().apply {
            epoch = saved[0] as Int
            expectedSource = (saved[1] as String).ifEmpty { null }
            expectedMedia = (saved[2] as String).ifEmpty { null }
            handledIdentity = (saved[3] as String).ifEmpty { null }
            manualWhileOpening = (saved[4] as? Int)?.decode()
            previousIdentity = (saved[5] as String).ifEmpty { null }
            selectedIdentity = (saved.getOrNull(6) as? String)?.ifEmpty { null }
            preferredLandscape = (saved.getOrNull(7) as? Int)?.decode()
        } })
        private fun Boolean?.encode() = when (this) { null -> -1; false -> 0; true -> 1 }
        private fun Int.decode(): Boolean? = when (this) { 0 -> false; 1 -> true; else -> null }
    }
}
