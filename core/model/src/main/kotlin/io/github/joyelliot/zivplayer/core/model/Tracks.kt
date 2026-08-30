// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.core.model

enum class TrackKind {
    AUDIO,
    VIDEO,
    SUBTITLE,
}

enum class TrackRole {
    DEFAULT,
    FORCED,
    COMMENTARY,
    HEARING_IMPAIRED,
    VISUAL_IMPAIRED,
}

class TrackDescriptor(
    val id: TrackId,
    val kind: TrackKind,
    val languageTag: String? = null,
    val label: String? = null,
    val codec: String? = null,
    val bitrate: Int? = null,
    val channels: Int? = null,
    val sampleRateHz: Int? = null,
    val width: Int? = null,
    val height: Int? = null,
    roles: Set<TrackRole> = emptySet(),
    val isExternal: Boolean = false,
) {
    val roles: Set<TrackRole> = buildSet {
        addAll(roles)
    }

    init {
        require(languageTag == null || languageTag.isNotBlank()) { "Language tag must not be blank." }
        require(label == null || label.isNotBlank()) { "Track label must not be blank." }
        require(codec == null || codec.isNotBlank()) { "Codec must not be blank." }
        require(bitrate == null || bitrate >= 0) { "Bitrate must not be negative." }
        require(channels == null || channels > 0) { "Channel count must be positive." }
        require(sampleRateHz == null || sampleRateHz > 0) { "Sample rate must be positive." }
        require(width == null || width > 0) { "Video width must be positive." }
        require(height == null || height > 0) { "Video height must be positive." }
    }

    override fun equals(other: Any?): Boolean =
        other is TrackDescriptor &&
            id == other.id &&
            kind == other.kind &&
            languageTag == other.languageTag &&
            label == other.label &&
            codec == other.codec &&
            bitrate == other.bitrate &&
            channels == other.channels &&
            sampleRateHz == other.sampleRateHz &&
            width == other.width &&
            height == other.height &&
            roles == other.roles &&
            isExternal == other.isExternal

    override fun hashCode(): Int {
        var result = id.hashCode()
        result = 31 * result + kind.hashCode()
        result = 31 * result + (languageTag?.hashCode() ?: 0)
        result = 31 * result + (label?.hashCode() ?: 0)
        result = 31 * result + (codec?.hashCode() ?: 0)
        result = 31 * result + (bitrate ?: 0)
        result = 31 * result + (channels ?: 0)
        result = 31 * result + (sampleRateHz ?: 0)
        result = 31 * result + (width ?: 0)
        result = 31 * result + (height ?: 0)
        result = 31 * result + roles.hashCode()
        result = 31 * result + isExternal.hashCode()
        return result
    }
}
