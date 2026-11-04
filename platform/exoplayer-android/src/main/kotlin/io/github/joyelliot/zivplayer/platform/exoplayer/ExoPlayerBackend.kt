// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import android.net.Uri
import android.os.Looper
import android.view.Surface
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaLibraryInfo
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.text.CueGroup
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.core.player.*
import io.github.joyelliot.zivplayer.core.player.runtime.*
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow

/** core 持有队列；此后端仅执行当前项，所有 Exo 访问都回到应用 Looper。 */
class ExoPlayerBackend(context: Context, private val onVideoFrameRendered: (Surface) -> Unit = {}) : PlayerBackend, PlaybackSurfacePort,
    PlaybackConfigurationPort, PlaybackCaptionPort {
    private val context = context.applicationContext
    private val dispatcher = Dispatchers.Main.immediate
    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val channel = Channel<BackendEvent>(256)
    override val events = channel.receiveAsFlow()
    private val mutableCaptions = MutableStateFlow(PlaybackCaptions())
    override val captions = mutableCaptions.asStateFlow()
    private var active: Active? = null
    private var closed = false
    private var broken = false
    private var options = PlaybackOptions()
    private var volume = VolumePercent.FULL
    private var muted = false
    private var rate = PlaybackRatePermille.NORMAL
    private var surface: Surface? = null
    private var videoOutput: OwnedVideoOutput? = null
    private var surfaceToken = 0L
    private var surfaceWidth = 0
    private var surfaceHeight = 0
    private val surfaceOwner = Any()
    private val subtitles = mutableListOf<SubtitleSource>()
    private val choices = mutableMapOf<TrackKind, TrackId?>()

    private class Active(val request: BackendLoadRequest, val player: ExoPlayer, var prepared: Boolean) {
        var failed = false
        var pendingSeek: BackendSeekRequest? = null
        var completedSeek: SeekGeneration? = null
        var tracks = TrackSnapshot.EMPTY
        var trackMap: Map<TrackId, ExoTrack> = emptyMap()
        var appliedChoices: Map<TrackKind, TrackId?> = emptyMap()
        var buffering: Boolean? = null
        var playing: Boolean? = null
        var ended = false
        var ticker: Job? = null
    }

    override suspend fun load(request: BackendLoadRequest) = withContext(dispatcher) {
        checkUsable()
        retirePlayer()
        subtitles.clear()
        choices.clear()
        install(request, request.startPosition.value, play = false)
    }

    override suspend fun play() = withContext(dispatcher) { current().player.play() }
    override suspend fun pause() = withContext(dispatcher) { current().player.pause() }

    override suspend fun seekTo(request: BackendSeekRequest) = withContext(dispatcher) {
        val state = current()
        check(state.request.generation == request.generation) { "The media changed before seeking." }
        check(state.pendingSeek == null) { "A seek is already pending." }
        state.pendingSeek = request
        state.ended = false
        mutableCaptions.value = PlaybackCaptions(state.request.item.id)
        state.player.setSeekParameters(if (request.precision == SeekPrecision.EXACT) SeekParameters.EXACT else SeekParameters.CLOSEST_SYNC)
        state.player.seekTo(request.position.value)
        // 同位置 seek 可能没有回调；READY 时完成的是定位操作，不宣称视频已呈现。
        completeSeekIfReady(state)
    }

    override suspend fun stop() = withContext(dispatcher) {
        checkUsable()
        retirePlayer()
        subtitles.clear()
        choices.clear()
    }

    override suspend fun setVolume(volume: VolumePercent) = withContext(dispatcher) {
        checkUsable(); this@ExoPlayerBackend.volume = volume; applyVolume()
    }
    override suspend fun setMuted(muted: Boolean) = withContext(dispatcher) {
        checkUsable(); this@ExoPlayerBackend.muted = muted; applyVolume()
    }
    override suspend fun setPlaybackRate(playbackRate: PlaybackRatePermille) = withContext(dispatcher) {
        checkUsable(); rate = playbackRate
        active?.player?.playbackParameters = PlaybackParameters(rate.value / 1000f)
    }

    override suspend fun selectTrack(kind: TrackKind, trackId: TrackId?) = withContext(dispatcher) {
        val state = current()
        require(kind != TrackKind.VIDEO && (trackId != null || kind == TrackKind.SUBTITLE)) { "Choose an audio or subtitle track." }
        if (trackId != null && state.trackMap[trackId]?.descriptor?.kind != kind) {
            // 添加字幕会重新准备媒体；core 的重播恢复可先于新的轨道发现。
            check(state.player.playbackState == Player.STATE_BUFFERING || state.player.playbackState == Player.STATE_IDLE) {
                "The requested track is unavailable."
            }
        }
        choices[kind] = trackId
        if (kind == TrackKind.SUBTITLE) mutableCaptions.value = PlaybackCaptions(state.request.item.id)
        applyChoices(state)
    }

    override suspend fun addSubtitle(source: SubtitleSource) = withContext(dispatcher) {
        var state = current()
        require(Uri.parse(source.locator).scheme in setOf("content", "file")) { "The subtitle source must be a readable URI." }
        subtitleMimeType(source.locator, source.title)
        val existing = subtitles.indexOfFirst { it.locator == source.locator }
        if (existing >= 0) {
            choices[TrackKind.SUBTITLE] = TrackId(externalId(existing))
            applyChoices(state)
        } else {
            check(subtitles.size < 16) { "At most 16 external subtitles may be attached." }
            validateSubtitle(context, source)
            val latest = current()
            check(latest.request.generation == state.request.generation) { "The media changed while reading the subtitle." }
            state = latest
            subtitles += source
            choices[TrackKind.SUBTITLE] = TrackId(externalId(subtitles.lastIndex))
            restartCurrent(state)
        }
    }

    override suspend fun attachSurface(surface: Surface): PlaybackSurfaceLease = withContext(dispatcher) {
        attachSurfaceNow(surface)
    }

    override fun attachSurfaceNow(surface: Surface): PlaybackSurfaceLease {
        check(Looper.myLooper() === Looper.getMainLooper()) { "Surface ownership must change on the main thread." }
        checkUsable(); require(surface.isValid) { "The video output is unavailable." }
        val replacement = OwnedVideoOutput(surface)
        try { active?.player?.setVideoSurface(replacement.surface) }
        catch (failure: Throwable) { replacement.close(); throw failure }
        val previous = videoOutput
        videoOutput = replacement
        this@ExoPlayerBackend.surface = surface
        previous?.close()
        return PlaybackSurfaceLease(surfaceOwner, ++surfaceToken)
    }

    override suspend fun detachSurface(lease: PlaybackSurfaceLease) = withContext(dispatcher) {
        detachSurfaceNow(lease)
    }

    override fun detachSurfaceNow(lease: PlaybackSurfaceLease) {
        check(Looper.myLooper() === Looper.getMainLooper()) { "Surface ownership must change on the main thread." }
        if (lease.owner === surfaceOwner && lease.token == surfaceToken) {
            videoOutput?.let { active?.player?.clearVideoSurface(it.surface); it.close() }
            videoOutput = null
            surface = null; surfaceWidth = 0; surfaceHeight = 0; surfaceToken++
        }
    }

    override suspend fun resizeSurface(lease: PlaybackSurfaceLease, width: Int, height: Int) = withContext(dispatcher) {
        require(width in 1..32768 && height in 1..32768)
        if (lease.owner === surfaceOwner && lease.token == surfaceToken) {
            surfaceWidth = width; surfaceHeight = height
        }
        // SurfaceHolder 拥有缓冲区尺寸，MediaCodec 不使用 mpv 的强制重绘命令。
    }

    override suspend fun configure(options: PlaybackOptions, resources: PlaybackResourcePaths): String? = withContext(dispatcher) {
        checkUsable()
        val rebuild = this@ExoPlayerBackend.options.decoder != options.decoder ||
            this@ExoPlayerBackend.options.decoderFallback != options.decoderFallback ||
            this@ExoPlayerBackend.options.cacheSizeMiB != options.cacheSizeMiB ||
            this@ExoPlayerBackend.options.audioDelayMs != options.audioDelayMs ||
            this@ExoPlayerBackend.options.subtitleDelayMs != options.subtitleDelayMs
        this@ExoPlayerBackend.options = options
        if (rebuild) active?.let(::restartCurrent) else active?.let(::applyRuntimeOptions)
        unsupportedSettings(options)
    }

    override suspend fun readDiagnostics(): PlaybackDiagnostics = withContext(dispatcher) {
        val player = active?.takeUnless { it.failed }?.player
        val video = player?.videoFormat
        val audio = player?.audioFormat
        val color = video?.colorInfo
        PlaybackDiagnostics(
            videoCodec = video?.sampleMimeType, audioCodec = audio?.sampleMimeType,
            decoder = player?.videoDecoderCounters?.let { counters ->
                if (counters.decoderInitCount > 0) selectedVideoDecoder else null
            },
            videoWidth = video?.width?.takeIf { it > 0 }, videoHeight = video?.height?.takeIf { it > 0 },
            sourceFramesPerSecond = video?.frameRate?.toDouble()?.takeIf { it > 0 },
            droppedFrames = player?.videoDecoderCounters?.droppedBufferCount?.toLong(),
            decoderDroppedFrames = player?.videoDecoderCounters?.droppedInputBufferCount?.toLong(),
            cacheDurationSeconds = player?.totalBufferedDuration?.coerceAtLeast(0)?.div(1000.0),
            colorPrimaries = color?.colorSpace?.let { when (it) {
                C.COLOR_SPACE_BT2020 -> "BT.2020"; C.COLOR_SPACE_BT709 -> "BT.709"; C.COLOR_SPACE_BT601 -> "BT.601"; else -> null
            } },
            transferFunction = color?.colorTransfer?.let { when (it) {
                C.COLOR_TRANSFER_ST2084 -> "PQ"; C.COLOR_TRANSFER_HLG -> "HLG"; C.COLOR_TRANSFER_SDR -> "SDR"; else -> null
            } },
            videoBitrate = video?.bitrate?.takeIf { it >= 0 }?.toLong(),
            audioBitrate = audio?.bitrate?.takeIf { it >= 0 }?.toLong(),
            engineVersion = "Media3 ExoPlayer ${MediaLibraryInfo.VERSION}",
            videoOutput = surface?.let { "MediaCodec / Surface" }, audioOutput = player?.let { "AudioTrack" },
        )
    }

    private var selectedVideoDecoder: String? = null

    override suspend fun close() = withContext(NonCancellable + dispatcher) {
        if (!closed) {
            closed = true
            try { retirePlayer() } finally {
                surface = null; surfaceToken++
                videoOutput?.close(); videoOutput = null
                channel.close(); scope.cancel()
            }
        }
    }

    private fun install(request: BackendLoadRequest, positionMs: Long, play: Boolean, prepared: Boolean = false,
        pendingSeek: BackendSeekRequest? = null, completedSeek: SeekGeneration? = null) {
        val renderers = DelayRenderersFactory(context, options.audioDelayMs).setEnableDecoderFallback(options.decoderFallback)
            .setMediaCodecSelector { mimeType, secure, tunneled ->
                MediaCodecSelector.DEFAULT.getDecoderInfos(mimeType, secure, tunneled).filter {
                    options.decoder != DecoderMode.SOFTWARE || it.softwareOnly
                }
            }
        val loadControl = playbackLoadControlBuilder(options).build()
        val player = ExoPlayer.Builder(context, renderers).setLooper(Looper.getMainLooper())
            .setLoadControl(loadControl).build()
        val state = Active(request, player, prepared).also {
            it.pendingSeek = pendingSeek; it.completedSeek = completedSeek
        }
        active = state
        selectedVideoDecoder = null
        try {
            player.setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA)
                .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), false)
            player.setHandleAudioBecomingNoisy(false)
            player.repeatMode = Player.REPEAT_MODE_OFF
            applyRuntimeOptions(state)
            player.playbackParameters = PlaybackParameters(rate.value / 1000f)
            player.setSeekParameters(if (pendingSeek?.precision == SeekPrecision.KEYFRAME) SeekParameters.CLOSEST_SYNC else SeekParameters.EXACT)
            applyVolume()
            videoOutput?.takeIf { it.source.isValid }?.surface?.let(player::setVideoSurface)
            player.addAnalyticsListener(object : androidx.media3.exoplayer.analytics.AnalyticsListener {
                override fun onRenderedFirstFrame(eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                    output: Any, renderTimeMs: Long) {
                    if (isCurrent(state)) videoOutput?.takeIf { it.accepts(output) }?.let { onVideoFrameRendered(it.source) }
                }
                override fun onVideoDecoderInitialized(eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                    decoderName: String, initializedTimestampMs: Long, initializationDurationMs: Long) {
                    if (isCurrent(state)) selectedVideoDecoder = decoderName
                }
            })
            player.addListener(object : Player.Listener {
                override fun onEvents(player: Player, events: Player.Events) {
                    if (isCurrent(state)) observe(state)
                }
                override fun onCues(cueGroup: CueGroup) {
                    if (isCurrent(state)) mutableCaptions.value = PlaybackCaptions(request.item.id, cueGroup)
                }
                override fun onPlayerError(error: PlaybackException) {
                    if (isCurrent(state)) fail(state, error)
                }
            })
            val media = MediaItem.Builder().setUri(request.item.media.source.locator)
                .setMediaId("ziv:${request.generation.value}")
                .setMimeType(request.item.media.source.mimeType)
                .setSubtitleConfigurations(subtitles.mapIndexed { index, source ->
                    MediaItem.SubtitleConfiguration.Builder(Uri.parse(source.locator))
                        .setId(externalId(index)).setLabel(source.title)
                        .setMimeType(subtitleMimeType(source.locator, source.title)).build()
                }).build()
            player.setMediaSource(playbackMediaSource(context, media, options.subtitleDelayMs), positionMs)
            player.playWhenReady = play
            player.prepare()
            state.ticker = scope.launch {
                while (isCurrent(state)) {
                    delay(250)
                    if (isCurrent(state) && state.prepared && state.pendingSeek == null && !state.ended) publishPosition(state)
                }
            }
        } catch (failure: Throwable) {
            retirePlayer()
            throw failure
        }
    }

    private fun restartCurrent(previous: Active) {
        val position = previous.pendingSeek?.position?.value ?: previous.player.currentPosition.coerceAtLeast(0)
        val play = previous.player.playWhenReady
        val request = previous.request
        val prepared = previous.prepared
        val pending = previous.pendingSeek
        val completed = previous.completedSeek
        retirePlayer()
        install(request, position, play, prepared, pending, completed)
    }

    private fun observe(state: Active) {
        val player = state.player
        if (player.currentMediaItem?.mediaId != "ziv:${state.request.generation.value}") return
        val (tracks, map) = player.currentTracks.toBackendTracks(subtitles.indices.map(::externalId).toSet())
        state.trackMap = map
        applyChoices(state)
        if (tracks != state.tracks) {
            state.tracks = tracks
            if (state.prepared) emit(BackendEvent.TracksChanged(state.request.generation, tracks))
        }
        if (player.playbackState == Player.STATE_READY && !state.prepared) {
            state.prepared = true
            emit(BackendEvent.Prepared(state.request.generation, duration(state), tracks, capabilities(player)))
        }
        if (!state.prepared) return
        val buffering = player.playbackState == Player.STATE_BUFFERING
        if (state.buffering != buffering) {
            state.buffering = buffering
            emit(BackendEvent.BufferingChanged(state.request.generation, buffering))
        }
        completeSeekIfReady(state)
        if (player.playbackState == Player.STATE_ENDED && !state.ended) {
            state.ended = true
            publishPosition(state)
            emit(BackendEvent.Ended(state.request.generation))
            mutableCaptions.value = PlaybackCaptions(state.request.item.id)
        } else if (state.playing != player.isPlaying) {
            state.playing = player.isPlaying
            if (player.isPlaying) emit(BackendEvent.PlaybackStarted(state.request.generation))
            else if (!player.playWhenReady) emit(BackendEvent.PlaybackPaused(state.request.generation))
        }
    }

    private fun applyChoices(state: Active) {
        val applicable = choices.filter { (_, id) -> id == null || id in state.trackMap }
        if (applicable == state.appliedChoices) return
        state.appliedChoices = applicable.toMap()
        state.player.trackSelectionParameters = state.player.trackSelectionParameters.buildUpon().apply {
            applicable.forEach { (kind, id) ->
                clearOverridesOfType(kind.media3Type())
                setTrackTypeDisabled(kind.media3Type(), id == null)
                id?.let(state.trackMap::get)?.let { setOverrideForType(TrackSelectionOverride(it.group, it.indices)) }
            }
        }.build()
    }

    private fun applyRuntimeOptions(state: Active) {
        state.player.setVideoChangeFrameRateStrategy(if (options.matchFrameRate)
            C.VIDEO_CHANGE_FRAME_RATE_STRATEGY_ONLY_IF_SEAMLESS else C.VIDEO_CHANGE_FRAME_RATE_STRATEGY_OFF)
        state.player.skipSilenceEnabled = options.skipSilence && options.audioDelayMs == 0
        state.player.trackSelectionParameters = state.player.trackSelectionParameters.buildUpon()
            .setPreferredAudioLanguages(*options.preferredAudioLanguage.tag.takeIf { it.isNotEmpty() }?.let { arrayOf(it) }.orEmpty())
            .setPreferredTextLanguages(*options.preferredSubtitleLanguage.tag.takeIf { it.isNotEmpty() }?.let { arrayOf(it) }.orEmpty())
            .build()
    }

    private fun completeSeekIfReady(state: Active) {
        val request = state.pendingSeek ?: return
        if (state.player.playbackState != Player.STATE_READY && state.player.playbackState != Player.STATE_ENDED) return
        state.pendingSeek = null
        state.completedSeek = request.seekGeneration
        emit(BackendEvent.SeekCompleted(request.generation, request.seekGeneration, position(state)))
        publishPosition(state)
    }

    private fun publishPosition(state: Active) {
        emit(BackendEvent.PositionChanged(state.request.generation, position(state), duration(state),
            Milliseconds(state.player.bufferedPosition.coerceAtLeast(0)), state.completedSeek), positionOnly = true)
    }
    private fun position(state: Active) = Milliseconds(state.player.currentPosition.coerceAtLeast(0))
    private fun duration(state: Active) = state.player.duration.takeIf { it != C.TIME_UNSET && it >= 0 }?.let(::Milliseconds)
    private fun applyVolume() { active?.player?.volume = if (muted) 0f else volume.value / 100f }
    private fun isCurrent(state: Active) = active === state && !closed && !broken && !state.failed
    private fun current(): Active { checkUsable(); return checkNotNull(active?.takeUnless { it.failed }) { "No media is loaded." } }
    private fun checkUsable() { check(!closed && !broken) { "The playback engine must be recreated." } }
    private fun externalId(index: Int) = "external:$index"

    private fun retirePlayer() {
        val previous = active
        active = null
        mutableCaptions.value = PlaybackCaptions()
        previous?.ticker?.cancel()
        previous?.player?.release()
    }

    private fun fail(state: Active, error: PlaybackException) {
        state.failed = true
        state.ticker?.cancel()
        mutableCaptions.value = PlaybackCaptions()
        // 原异常可能包含 URI；只发布播放器自己的消息和数字错误码。
        emit(BackendEvent.Failure(state.request.generation, PlayerError(PlayerErrorKind.SOURCE_UNAVAILABLE,
            "媒体无法播放（错误代码 ${error.errorCode}）。请检查文件权限或设备的格式支持。", ErrorRecovery.RETRY)))
    }

    private fun emit(event: BackendEvent, positionOnly: Boolean = false) {
        if (channel.trySend(event).isSuccess || positionOnly || closed || broken) return
        broken = true
        active?.player?.pause()
        mutableCaptions.value = PlaybackCaptions()
        channel.close(IllegalStateException("The playback event queue overflowed."))
    }

    private fun capabilities(player: Player) = PlayerCapabilities.of(buildSet {
        addAll(listOf(PlayerCapability.PLAY, PlayerCapability.PAUSE, PlayerCapability.STOP,
            PlayerCapability.SET_VOLUME, PlayerCapability.SET_MUTED, PlayerCapability.SET_RATE,
            PlayerCapability.SELECT_TRACK, PlayerCapability.ADD_SUBTITLE))
        if (player.isCurrentMediaItemSeekable) add(PlayerCapability.SEEK)
    })
}
