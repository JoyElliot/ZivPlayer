// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.decoder.DecoderInputBuffer
import androidx.media3.exoplayer.FormatHolder
import androidx.media3.exoplayer.LoadingInfo
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.source.*
import androidx.media3.exoplayer.trackselection.ExoTrackSelection
import androidx.media3.exoplayer.upstream.Allocator

internal fun playbackMediaSource(context: Context, item: MediaItem, subtitleDelayMs: Int): MediaSource {
    val factory = DefaultMediaSourceFactory(context)
    if (subtitleDelayMs == 0) return sourceWithExternalSubtitles(context, item)
    // 字幕独立寻址才能提前读取；只增加一个解封装源，不增加视频解码器。
    val av = FilteringMediaSource(factory.createMediaSource(item.buildUpon()
        .setSubtitleConfigurations(emptyList()).build()), setOf(C.TRACK_TYPE_AUDIO, C.TRACK_TYPE_VIDEO,
        C.TRACK_TYPE_METADATA, C.TRACK_TYPE_CAMERA_MOTION, C.TRACK_TYPE_IMAGE))
    val text = FilteringMediaSource(sourceWithExternalSubtitles(context, item, loadOnlySelectedTracks = true), C.TRACK_TYPE_TEXT)
    // 在 PEEK 阶段裁掉片尾以后的字幕，让 TextRenderer 及时读到 EOS。
    return MergingMediaSource(false, true, av, SubtitleOffsetMediaSource(text, subtitleDelayMs * 1000L))
}

private fun sourceWithExternalSubtitles(context: Context, item: MediaItem,
    loadOnlySelectedTracks: Boolean = false): MediaSource {
    val subtitles = item.localConfiguration?.subtitleConfigurations.orEmpty().mapIndexed { index, subtitle ->
        if (subtitle.id != null) subtitle else subtitle.buildUpon().setId("zivplayer:sidecar:$index").build()
    }
    val factory = DefaultMediaSourceFactory(context).setLoadOnlySelectedTracks(loadOnlySelectedTracks)
    if (subtitles.isEmpty()) return factory.createMediaSource(item)
    factory.setSubtitleParserFactory(SidecarSubtitleParserFactory(subtitles.mapNotNull { it.id }.toSet()))
    return factory.createMediaSource(item.buildUpon().setSubtitleConfigurations(subtitles).build())
}

internal class SubtitleOffsetMediaSource(source: MediaSource, private val offsetUs: Long) : WrappingMediaSource(source) {
    override fun createPeriod(id: MediaSource.MediaPeriodId, allocator: Allocator, startPositionUs: Long): MediaPeriod =
        SubtitleOffsetPeriod(mediaSource.createPeriod(id, allocator, subtitleChildPosition(startPositionUs, offsetUs)), offsetUs)

    override fun releasePeriod(mediaPeriod: MediaPeriod) =
        mediaSource.releasePeriod((mediaPeriod as SubtitleOffsetPeriod).child)

    override fun getMediaTimeForChildMediaTime(mediaTimeMs: Long, mediaPeriodId: MediaSource.MediaPeriodId?): Long =
        subtitleShiftTime(mediaTimeMs, offsetUs / 1000)
}

/** 此 period 只提供 TEXT；主音视频 source 独占时长、定位结果与 discontinuity。 */
internal class SubtitleOffsetPeriod(val child: MediaPeriod, private val offsetUs: Long) : MediaPeriod by child,
    MediaPeriod.Callback {
    private lateinit var callback: MediaPeriod.Callback

    override fun prepare(callback: MediaPeriod.Callback, positionUs: Long) {
        this.callback = callback
        child.prepare(this, subtitleChildPosition(positionUs, offsetUs))
    }

    override fun selectTracks(selections: Array<out ExoTrackSelection?>, mayRetainStreamFlags: BooleanArray,
        streams: Array<SampleStream?>, streamResetFlags: BooleanArray, positionUs: Long): Long {
        val childStreams = Array(streams.size) { (streams[it] as? SubtitleOffsetStream)?.child }
        child.selectTracks(selections, mayRetainStreamFlags, childStreams, streamResetFlags,
            subtitleChildPosition(positionUs, offsetUs))
        streams.indices.forEach { index ->
            val next = childStreams[index]
            if (next == null) streams[index] = null
            else if ((streams[index] as? SubtitleOffsetStream)?.child !== next) {
                streams[index] = SubtitleOffsetStream(next, offsetUs)
            }
        }
        // 片头 P-d 可能被截到 0；字幕首帧仍在 +d，不能让此返回值推动音视频。
        return positionUs
    }

    override fun seekToUs(positionUs: Long): Long {
        child.seekToUs(subtitleChildPosition(positionUs, offsetUs))
        return positionUs
    }

    override fun getAdjustedSeekPositionUs(positionUs: Long, seekParameters: SeekParameters) = positionUs
    override fun readDiscontinuity(): Long { child.readDiscontinuity(); return C.TIME_UNSET }
    override fun discardBuffer(positionUs: Long, toKeyframe: Boolean) =
        child.discardBuffer(subtitleChildPosition(positionUs, offsetUs), toKeyframe)
    override fun getBufferedPositionUs() = subtitleShiftTime(child.bufferedPositionUs, offsetUs)
    override fun getNextLoadPositionUs() = subtitleShiftTime(child.nextLoadPositionUs, offsetUs)
    override fun continueLoading(loadingInfo: LoadingInfo) = child.continueLoading(loadingInfo.buildUpon()
        .setPlaybackPositionUs(subtitleChildPosition(loadingInfo.playbackPositionUs, offsetUs)).build())
    override fun reevaluateBuffer(positionUs: Long) = child.reevaluateBuffer(subtitleChildPosition(positionUs, offsetUs))
    override fun setEndPositionUs(endPositionUs: Long) =
        subtitleShiftTime(child.setEndPositionUs(subtitleChildPosition(endPositionUs, offsetUs)), offsetUs)
    override fun onPrepared(mediaPeriod: MediaPeriod) = callback.onPrepared(this)
    override fun onContinueLoadingRequested(source: MediaPeriod) = callback.onContinueLoadingRequested(this)
}

internal class SubtitleOffsetStream(val child: SampleStream, private val offsetUs: Long) : SampleStream by child {
    override fun readData(formatHolder: FormatHolder, buffer: DecoderInputBuffer, readFlags: Int): Int {
        val result = child.readData(formatHolder, buffer, readFlags)
        if (result == C.RESULT_BUFFER_READ && !buffer.isEndOfStream) {
            buffer.timeUs = subtitleShiftTime(buffer.timeUs, offsetUs)
        } else if (result == C.RESULT_FORMAT_READ) {
            // 旧式解码器的绝对字幕时间也必须偏移；相对 sample 的格式已由 timeUs 移动。
            formatHolder.format?.takeIf { it.subsampleOffsetUs != Format.OFFSET_SAMPLE_RELATIVE }?.let {
                formatHolder.format = it.buildUpon().setSubsampleOffsetUs(subtitleShiftTime(it.subsampleOffsetUs, offsetUs)).build()
            }
        }
        return result
    }

    override fun skipData(positionUs: Long) = child.skipData(subtitleChildPosition(positionUs, offsetUs))
}

internal fun subtitleShiftTime(timeUs: Long, offsetUs: Long): Long = when (timeUs) {
    C.TIME_UNSET, C.TIME_END_OF_SOURCE -> timeUs
    else -> timeUs + offsetUs
}

internal fun subtitleChildPosition(timeUs: Long, offsetUs: Long): Long = when (timeUs) {
    C.TIME_UNSET, C.TIME_END_OF_SOURCE -> timeUs
    else -> (timeUs - offsetUs).coerceAtLeast(0)
}
