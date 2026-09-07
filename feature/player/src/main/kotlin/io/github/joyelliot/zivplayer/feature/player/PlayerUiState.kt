// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.feature.player

import kotlin.math.roundToInt

enum class PlayerConnectionStatus {
    CONNECTING,
    CONNECTED,
    RECONNECTING,
}

enum class PlayerPlaybackStatus {
    EMPTY,
    LOADING,
    PLAYING,
    PAUSED,
    STOPPED,
    BUFFERING,
    ENDED,
    ERROR,
}

enum class PlayerRepeatMode {
    OFF,
    ONE,
    ALL,
}

enum class PlayerTrackKind { AUDIO, SUBTITLE }

data class PlayerQueueUiItem(val index: Int, val title: String, val selected: Boolean)

data class PlayerTrackUiItem(
    val id: String,
    val kind: PlayerTrackKind,
    val label: String,
    val selected: Boolean,
)

/** Media3-free state consumed by the player feature. */
data class PlayerUiState(
    val connectionStatus: PlayerConnectionStatus = PlayerConnectionStatus.CONNECTING,
    val playbackStatus: PlayerPlaybackStatus = PlayerPlaybackStatus.EMPTY,
    val mediaId: String? = null,
    val playbackIdentity: String? = null,
    val queue: List<PlayerQueueUiItem> = emptyList(),
    val canPrevious: Boolean = false,
    val canNext: Boolean = false,
    val canSelectQueueItem: Boolean = false,
    val title: String? = null,
    val supportingText: String? = null,
    val positionMs: Long = 0L,
    val durationMs: Long? = null,
    val bufferedPositionMs: Long? = null,
    val playWhenReady: Boolean = false,
    val playbackSpeed: Float = 1f,
    val volume: Float = 1f,
    val repeatMode: PlayerRepeatMode = PlayerRepeatMode.OFF,
    val canPlayPause: Boolean = false,
    val canStop: Boolean = false,
    val canSeek: Boolean = false,
    val canSetSpeed: Boolean = false,
    val canSetVolume: Boolean = false,
    val canSetRepeat: Boolean = false,
    val canRenderVideo: Boolean = false,
    val hasVideo: Boolean = false,
    val videoAspectRatio: Float = 16f / 9f,
    val tracks: List<PlayerTrackUiItem> = emptyList(),
    val canSelectTracks: Boolean = false,
    val canAddSubtitle: Boolean = false,
    val subtitleMessage: String? = null,
    val errorMessage: String? = null,
) {
    val hasMedia: Boolean
        get() = mediaId != null

    val timelineProgress: Float
        get() {
            val duration = durationMs?.takeIf { it > 0L } ?: return 0f
            return (positionMs.coerceIn(0L, duration).toDouble() / duration.toDouble())
                .toFloat()
        }
}

data class RecentMediaUiItem(
    val mediaId: String,
    val title: String,
    val supportingText: String? = null,
    val positionMs: Long? = null,
    val durationMs: Long? = null,
    val completed: Boolean = false,
) {
    val progress: Float?
        get() {
            val duration = durationMs?.takeIf { it > 0L } ?: return null
            val position = positionMs ?: return null
            return (position.coerceIn(0L, duration).toDouble() / duration.toDouble()).toFloat()
        }
}

fun formatPlaybackTime(milliseconds: Long): String {
    val totalSeconds = milliseconds.coerceAtLeast(0L) / MILLIS_PER_SECOND
    val seconds = totalSeconds % SECONDS_PER_MINUTE
    val totalMinutes = totalSeconds / SECONDS_PER_MINUTE
    val minutes = totalMinutes % MINUTES_PER_HOUR
    val hours = totalMinutes / MINUTES_PER_HOUR
    return if (hours > 0L) {
        "$hours:${minutes.twoDigits()}:${seconds.twoDigits()}"
    } else {
        "$minutes:${seconds.twoDigits()}"
    }
}

fun formatPlaybackSpeed(speed: Float): String {
    val hundredths = (speed.coerceIn(MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED) * 100f)
        .roundToInt()
    val whole = hundredths / 100
    val fraction = hundredths % 100
    val value = when {
        fraction == 0 -> whole.toString()
        fraction % 10 == 0 -> "$whole.${fraction / 10}"
        else -> "$whole.${fraction.twoDigits()}"
    }
    return "${value}×"
}

fun nextPlaybackSpeed(speed: Float): Float = PLAYBACK_SPEED_STEPS
    .firstOrNull { it > speed + PLAYBACK_SPEED_EPSILON }
    ?: PLAYBACK_SPEED_STEPS.first()

private fun Long.twoDigits(): String = toString().padStart(2, '0')

private fun Int.twoDigits(): String = toString().padStart(2, '0')

private const val MILLIS_PER_SECOND = 1_000L
private const val SECONDS_PER_MINUTE = 60L
private const val MINUTES_PER_HOUR = 60L
private const val MIN_PLAYBACK_SPEED = 0.25f
private const val MAX_PLAYBACK_SPEED = 4f
private const val PLAYBACK_SPEED_EPSILON = 0.001f
private val PLAYBACK_SPEED_STEPS = listOf(0.5f, 0.75f, 1f, 1.25f, 1.5f, 2f)
