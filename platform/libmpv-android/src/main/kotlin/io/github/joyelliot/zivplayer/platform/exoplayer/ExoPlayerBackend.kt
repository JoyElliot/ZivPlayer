// SPDX-License-Identifier: GPL-3.0-or-later

@file:androidx.annotation.OptIn(markerClass = [
    androidx.media3.common.util.UnstableApi::class,
    androidx.media3.common.util.ExperimentalApi::class,
])

package io.github.joyelliot.zivplayer.platform.exoplayer

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.view.Surface
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaLibraryInfo
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.common.PlaybackException
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.TrackSelectionParameters.AudioOffloadPreferences
import androidx.media3.common.text.CueGroup
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.DecoderCounters
import androidx.media3.exoplayer.ScrubbingModeParameters
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.extractor.DefaultExtractorsFactory
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.core.player.*
import io.github.joyelliot.zivplayer.core.player.runtime.*
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvConfigurationPort
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvResourcePaths
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvSurfaceLease
import io.github.joyelliot.zivplayer.platform.libmpv.LibmpvSurfacePort
import io.github.peerless2012.ass.media.AssHandler
import io.github.peerless2012.ass.media.kt.withAssMkvSupport
import io.github.peerless2012.ass.media.kt.withAssSupport
import io.github.peerless2012.ass.media.parser.AssSubtitleParserFactory
import io.github.peerless2012.ass.media.type.AssRenderType
import java.io.File
import java.io.InputStream
import java.io.ByteArrayOutputStream
import java.util.concurrent.FutureTask
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * Experimental Android backend. One ExoPlayer item mirrors the current core queue item;
 * the core/service retain queue, history, audio focus and playback intent ownership.
 */
class ExoPlayerBackend(context: Context) : PlayerBackend, LibmpvSurfacePort,
    LibmpvConfigurationPort, DirectScrubPort {
    private val context = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val eventChannel = Channel<BackendEvent>(Channel.UNLIMITED)
    override val events = eventChannel.receiveAsFlow()
    override val supportsInFlightSeekReplacement = true
    private var player: ExoPlayer? = null
    private var ass: AssHandler? = null
    private var request: BackendLoadRequest? = null
    private var prepared = false
    private var closed = false
    private var scrubbing = false
    private var options = PlaybackOptions()
    private var resourcePaths = LibmpvResourcePaths()
    private var surface: Surface? = null
    private var lease: LibmpvSurfaceLease? = null
    private var leaseSerial = 0L
    private var surfaceWidth = 0
    private var surfaceHeight = 0
    private var volume = VolumePercent.FULL
    private var muted = false
    private var rate = PlaybackRatePermille.NORMAL
    private var seekEpoch: SeekGeneration? = null
    private var pendingSeek: BackendSeekRequest? = null
    private var pendingSeekHasFrame = false
    private var pendingSeekUsesSyncFrame = false
    private var previousPreviewPosition: Long? = null
    private var previousPreviewTimeMs = 0L
    private var lastFrameMs = Long.MIN_VALUE
    private var lastCompletedExactPosition: Long? = null
    private val seekSerial = AtomicLong()
    private val temporarySubtitles = mutableListOf<File>()
    private val externalSubtitles = mutableListOf<MediaItem.SubtitleConfiguration>()
    private var pendingSubtitleSelection: String? = null
    private val tracksById = mutableMapOf<TrackId, Pair<Tracks.Group, Int>>()
    private val desiredTracks = mutableMapOf<TrackKind, TrackId?>()
    private var decoderName: String? = null
    private var audioOffloaded: Boolean? = null
    private var droppedFrames = 0L

    override val configurationMessage: String
        get() = "ExoPlayer + libass 实验：原始视频 Surface；mpv 着色器、缩放滤镜、色调映射选项、音频延迟及部分字幕覆盖样式尚未接入。"

    private val positionPoll = object : Runnable {
        override fun run() {
            val active = player
            val load = request
            if (active != null && load != null && prepared && !closed) {
                if (!scrubbing && pendingSeek == null) eventChannel.trySend(BackendEvent.PositionChanged(
                    load.generation, position(active), duration(active),
                    Milliseconds(active.bufferedPosition.coerceAtLeast(0)), seekEpoch))
                // ASS samples may arrive after READY while paused, with no new
                // renderer clock tick. libass's unchanged-frame path avoids a redraw.
                if (!active.isPlaying) ass?.let(::redrawSubtitles)
                completeSeekIfReady(active)
                main.postDelayed(this, 100)
            }
        }
    }

    override suspend fun load(request: BackendLoadRequest) = withContext(Dispatchers.Main.immediate) {
        check(!closed)
        releasePlayer()
        this@ExoPlayerBackend.request = request
        prepared = false
        seekEpoch = null
        pendingSeek = null
        lastFrameMs = Long.MIN_VALUE
        lastCompletedExactPosition = null
        externalSubtitles.clear()
        pendingSubtitleSelection = null
        desiredTracks.clear()
        deleteTemporarySubtitles()
        decoderName = null
        audioOffloaded = null
        droppedFrames = 0
        ExoFrameObservation.lastError = null
        val handler = AssHandler(AssRenderType.OVERLAY_OPEN_GL)
        ass = handler
        val parserFactory = AssSubtitleParserFactory(handler)
        val httpFactory = DefaultHttpDataSource.Factory().setDefaultRequestProperties(request.item.media.source.httpHeaders)
        val sourceFactory = DefaultMediaSourceFactory(DefaultDataSource.Factory(context, httpFactory),
            DefaultExtractorsFactory().withAssMkvSupport(parserFactory, handler))
            .setSubtitleParserFactory(parserFactory)
        val renderers = ExoRenderersFactory(context).setEnableDecoderFallback(true)
        if (options.decoder == DecoderMode.SOFTWARE) renderers.setMediaCodecSelector(
            MediaCodecSelector { mime, secure, tunnel ->
                MediaCodecSelector.DEFAULT.getDecoderInfos(mime, secure, tunnel).filter { it.softwareOnly }
            })
        val active = ExoPlayer.Builder(context)
            .setLooper(Looper.getMainLooper())
            .experimentalSetDynamicSchedulingEnabled(true)
            .setMediaSourceFactory(sourceFactory)
            .setRenderersFactory(renderers.withAssSupport(handler))
            .setHandleAudioBecomingNoisy(false)
            .build()
        player = active
        handler.init(active)
        ExoSubtitleOutput.state.value = ExoSubtitleOutput.State(this@ExoPlayerBackend, handler)
        handler.renderCallback = { render ->
            render?.setFontScale(options.subtitleScalePercent / 100f)
            main.post { if (ass === handler) redrawSubtitles(handler) }
        }
        active.setAudioAttributes(androidx.media3.common.AudioAttributes.Builder()
            .setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), false)
        active.volume = if (muted) 0f else volume.value / 100f
        active.setPlaybackSpeed(rate.value / 1000f)
        active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
            .setPreferredTextLanguages(java.util.Locale.getDefault().language, "zh", "en")
            // Eligible audio-only playback may use the device DSP. Media3 keeps
            // regular AudioTrack playback when video/text or the format prevents offload.
            .setAudioOffloadPreferences(AudioOffloadPreferences.Builder()
                .setAudioOffloadMode(AudioOffloadPreferences.AUDIO_OFFLOAD_MODE_ENABLED).build())
            .setSelectUndeterminedTextLanguage(true).build()
        active.scrubbingModeParameters = ScrubbingModeParameters.DEFAULT
        active.setVideoFrameMetadataListener { timeUs, _, _, _ ->
            val serial = seekSerial.get()
            main.post {
                if (player !== active || closed) return@post
                val frameMs = (timeUs / 1000).coerceAtLeast(0)
                ExoFrameObservation.positionMs = frameMs
                ExoFrameObservation.releaseSerial++
                if (serial != seekSerial.get()) return@post
                lastFrameMs = frameMs
                val pending = pendingSeek
                if (pending != null && serial == seekSerial.get() &&
                    abs(lastFrameMs - if (pendingSeekUsesSyncFrame) active.currentPosition else pending.position.value) <= 120) {
                    pendingSeekHasFrame = true
                    // The callback precedes release to the Surface. The UI/device benchmark
                    // reads the producer buffer separately; this is a decoder release fence.
                    main.post {
                        if (player === active && serial == seekSerial.get() && pendingSeek === pending)
                            completeSeekIfReady(active)
                    }
                }
            }
        }
        active.addAnalyticsListener(object : AnalyticsListener {
            // AudioTrack release can arrive after its replacement was initialized.
            // Clear on renderer disable/teardown rather than a late release event.
            override fun onAudioTrackInitialized(eventTime: AnalyticsListener.EventTime,
                audioTrackConfig: AudioSink.AudioTrackConfig) {
                if (player === active) audioOffloaded = audioTrackConfig.offload
            }
            override fun onAudioDisabled(eventTime: AnalyticsListener.EventTime,
                decoderCounters: DecoderCounters) {
                if (player === active) audioOffloaded = null
            }
            override fun onVideoDecoderInitialized(eventTime: AnalyticsListener.EventTime,
                decoderName: String, initializedTimestampMs: Long, initializationDurationMs: Long) {
                if (player === active) this@ExoPlayerBackend.decoderName = decoderName
            }
            override fun onDroppedVideoFrames(eventTime: AnalyticsListener.EventTime, droppedFrames: Int,
                elapsedMs: Long) { if (player === active) this@ExoPlayerBackend.droppedFrames += droppedFrames }
        })
        active.addListener(object : Player.Listener {
            private fun valid() = player === active && this@ExoPlayerBackend.request?.generation == request.generation && !closed
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (!valid()) return
                if (playbackState == Player.STATE_READY && !prepared) {
                    prepared = true
                    eventChannel.trySend(BackendEvent.Prepared(request.generation, duration(active),
                        trackSnapshot(active), capabilities(active)))
                    main.removeCallbacks(positionPoll)
                    main.post(positionPoll)
                }
                if (prepared && !scrubbing) eventChannel.trySend(BackendEvent.BufferingChanged(request.generation,
                    playbackState == Player.STATE_BUFFERING))
                if (playbackState == Player.STATE_ENDED) eventChannel.trySend(BackendEvent.Ended(request.generation))
                if (playbackState == Player.STATE_READY) redrawSubtitles(handler)
                completeSeekIfReady(active)
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (!valid() || !prepared || scrubbing) return
                eventChannel.trySend(if (isPlaying) BackendEvent.PlaybackStarted(request.generation)
                    else BackendEvent.PlaybackPaused(request.generation))
            }
            override fun onTracksChanged(tracks: Tracks) {
                if (!valid()) return
                val snapshot = trackSnapshot(active)
                // Adding a sidecar rebuilds Media3 track groups. Rebind explicit
                // choices to the new groups, including replay's audio/off choices.
                desiredTracks.forEach { (kind, id) ->
                    if (id == null || tracksById.containsKey(id)) applyTrackChoice(active, kind, id)
                }
                pendingSubtitleSelection?.let { subtitleId ->
                    tracks.groups.firstOrNull { group -> group.type == C.TRACK_TYPE_TEXT &&
                        (0 until group.length).any { group.getTrackFormat(it).id?.endsWith(subtitleId) == true }
                    }?.let { group ->
                        val index = (0 until group.length).first { group.getTrackFormat(it).id?.endsWith(subtitleId) == true }
                        pendingSubtitleSelection = null
                        active.trackSelectionParameters = active.trackSelectionParameters.buildUpon()
                            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                            .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, index)).build()
                    }
                }
                if (prepared) eventChannel.trySend(BackendEvent.TracksChanged(request.generation, snapshot))
                redrawSubtitles(handler)
            }
            override fun onCues(cueGroup: CueGroup) {
                if (valid()) ExoSubtitleOutput.state.value = ExoSubtitleOutput.State(this@ExoPlayerBackend, handler, cueGroup)
            }
            override fun onPlayerError(error: PlaybackException) {
                ExoFrameObservation.lastError = generateSequence<Throwable>(error) { it.cause }
                    .joinToString(" / ") { "${it.javaClass.simpleName}: ${it.message}" }
                if (valid()) eventChannel.trySend(BackendEvent.Failure(request.generation,
                    PlayerError(PlayerErrorKind.BACKEND_OPERATION_FAILED,
                        "ExoPlayer playback failed (${error.errorCodeName}).", ErrorRecovery.RESET)))
            }
        })
        surface?.takeIf(Surface::isValid)?.let(active::setVideoSurface)
        active.setMediaItem(MediaItem.Builder().setMediaId(request.item.id.value)
            .setUri(request.item.media.source.locator).setMimeType(request.item.media.source.mimeType).build(), request.startPosition.value)
        // Media transition clears the extension's pending font list, so add afterwards.
        loadFonts(handler)
        active.prepare()
    }

    private fun capabilities(active: ExoPlayer): PlayerCapabilities {
        val values = mutableSetOf(PlayerCapability.PLAY, PlayerCapability.PAUSE, PlayerCapability.STOP,
            PlayerCapability.SET_VOLUME, PlayerCapability.SET_MUTED, PlayerCapability.SET_RATE,
            PlayerCapability.SELECT_TRACK, PlayerCapability.ADD_SUBTITLE)
        if (active.isCurrentMediaItemSeekable) values += PlayerCapability.SEEK
        return PlayerCapabilities.of(values)
    }

    override suspend fun play() = withContext(Dispatchers.Main.immediate) {
        endScrubbing()
        player?.play() ?: error("No active ExoPlayer.")
    }
    override suspend fun pause() = withContext(Dispatchers.Main.immediate) { player?.pause(); Unit }

    override suspend fun seekTo(request: BackendSeekRequest) = withContext(Dispatchers.Main.immediate) {
        check(this@ExoPlayerBackend.request?.generation == request.generation)
        val active = checkNotNull(player)
        if (request.precision == SeekPrecision.EXACT) endScrubbing() else beginScrubbing()
        val now = SystemClock.elapsedRealtime()
        pendingSeekUsesSyncFrame = request.precision == SeekPrecision.KEYFRAME &&
            previousPreviewPosition?.let { previous ->
                abs(request.position.value - previous).toDouble() /
                    (now - previousPreviewTimeMs).coerceAtLeast(1) > 100.0
            } == true
        if (request.precision == SeekPrecision.KEYFRAME) {
            previousPreviewPosition = request.position.value
            previousPreviewTimeMs = now
        }
        seekSerial.incrementAndGet()
        pendingSeek = request
        seekEpoch = request.seekGeneration
        // ExoPlayer may elide a seek to the current position. Reuse only a known
        // exact result or the very same frame timestamp, never a nearby preview.
        pendingSeekHasFrame = active.playbackState == Player.STATE_READY &&
            active.currentPosition == request.position.value &&
            (lastFrameMs == request.position.value || lastCompletedExactPosition == request.position.value)
        lastCompletedExactPosition = null
        // Ordinary dragging decodes the target frame. Scanning faster than 100x
        // uses nearby sync frames so a whole-film sweep does not build decode lag.
        active.setSeekParameters(if (pendingSeekUsesSyncFrame) SeekParameters.CLOSEST_SYNC else SeekParameters.EXACT)
        active.seekTo(request.position.value)
        main.post { if (player === active) completeSeekIfReady(active) }
        Unit
    }

    private fun completeSeekIfReady(active: ExoPlayer) {
        val pending = pendingSeek ?: return
        if (active !== player || active.playbackState != Player.STATE_READY) return
        val hasVideo = active.currentTracks.groups.any { it.type == C.TRACK_TYPE_VIDEO && it.isSelected }
        val retainedSyncFrame = pendingSeekUsesSyncFrame && abs(lastFrameMs - active.currentPosition) <= 120
        if (hasVideo && surface?.isValid == true && !pendingSeekHasFrame && !retainedSyncFrame) return
        pendingSeek = null
        if (pendingSeekHasFrame && !pendingSeekUsesSyncFrame) lastCompletedExactPosition = pending.position.value
        eventChannel.trySend(BackendEvent.SeekCompleted(pending.generation, pending.seekGeneration, position(active)))
    }

    override fun beginScrubbing() = onMain {
        if (!scrubbing) {
            previousPreviewPosition = player?.currentPosition
            previousPreviewTimeMs = SystemClock.elapsedRealtime()
        }
        scrubbing = true
        player?.setScrubbingModeEnabled(true)
        Unit
    }
    override fun endScrubbing() = onMain {
        if (scrubbing) {
            scrubbing = false
            player?.setScrubbingModeEnabled(false)
            player?.setSeekParameters(SeekParameters.EXACT)
            previousPreviewPosition = null
        }
    }

    override suspend fun stop() = withContext(Dispatchers.Main.immediate) {
        releasePlayer()
        request = null
        prepared = false
    }
    override suspend fun setVolume(volume: VolumePercent) = withContext(Dispatchers.Main.immediate) {
        this@ExoPlayerBackend.volume = volume
        player?.volume = if (muted) 0f else volume.value / 100f
    }
    override suspend fun setMuted(muted: Boolean) = withContext(Dispatchers.Main.immediate) {
        this@ExoPlayerBackend.muted = muted
        player?.volume = if (muted) 0f else volume.value / 100f
    }
    override suspend fun setPlaybackRate(playbackRate: PlaybackRatePermille) = withContext(Dispatchers.Main.immediate) {
        rate = playbackRate
        player?.setPlaybackSpeed(playbackRate.value / 1000f)
        Unit
    }

    override suspend fun selectTrack(kind: TrackKind, trackId: TrackId?) = withContext(Dispatchers.Main.immediate) {
        val active = checkNotNull(player)
        if (kind == TrackKind.SUBTITLE) pendingSubtitleSelection = null
        desiredTracks[kind] = trackId
        applyTrackChoice(active, kind, trackId)
    }

    private fun applyTrackChoice(active: ExoPlayer, kind: TrackKind, trackId: TrackId?) {
        val type = when (kind) { TrackKind.AUDIO -> C.TRACK_TYPE_AUDIO; TrackKind.VIDEO -> C.TRACK_TYPE_VIDEO; TrackKind.SUBTITLE -> C.TRACK_TYPE_TEXT }
        val builder = active.trackSelectionParameters.buildUpon().clearOverridesOfType(type).setTrackTypeDisabled(type, trackId == null)
        if (trackId != null) {
            val (group, index) = checkNotNull(tracksById[trackId]) { "Unavailable track." }
            check(group.type == type)
            builder.setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, index))
        }
        val parameters = builder.build()
        if (parameters != active.trackSelectionParameters) active.trackSelectionParameters = parameters
    }

    override suspend fun addSubtitle(source: SubtitleSource) {
        val file = withContext(Dispatchers.IO) {
            val bytes = if (source.locator.startsWith("fd://")) {
                val fd = source.locator.removePrefix("fd://").toInt()
                ParcelFileDescriptor.AutoCloseInputStream(ParcelFileDescriptor.fromFd(fd)).use(::readSubtitleBytes)
            } else context.contentResolver.openInputStream(android.net.Uri.parse(source.locator))!!.use(::readSubtitleBytes)
            val assText = bytes.take(2048).toByteArray().toString(Charsets.UTF_8).contains("[Script Info]", ignoreCase = true)
            File.createTempFile("ziv-exo-sub-", if (assText) ".ass" else ".srt", context.cacheDir).apply { writeBytes(bytes) }
        }
        withContext(Dispatchers.Main.immediate) {
            if (closed || player == null) { file.delete(); return@withContext }
            temporarySubtitles += file
            val active = checkNotNull(player)
            val subtitleId = "ziv-external-${128 + externalSubtitles.size}"
            pendingSubtitleSelection = subtitleId
            desiredTracks.remove(TrackKind.SUBTITLE)
            externalSubtitles += MediaItem.SubtitleConfiguration.Builder(android.net.Uri.fromFile(file))
                .setId(subtitleId)
                .setLabel(source.title ?: file.extension.uppercase())
                .setMimeType(if (file.extension == "ass") MimeTypes.TEXT_SSA else MimeTypes.APPLICATION_SUBRIP).build()
            val item = checkNotNull(active.currentMediaItem).buildUpon().setSubtitleConfigurations(externalSubtitles).build()
            val position = active.currentPosition
            audioOffloaded = null
            active.setMediaItem(item, position)
            loadFonts(checkNotNull(ass))
            active.prepare()
        }
    }

    override fun attachSurface(surface: Surface): LibmpvSurfaceLease = onMain {
        check(!closed && surface.isValid)
        player?.clearVideoSurface()
        this.surface = surface
        ExoSurfaceOutput.owner = this
        LibmpvSurfaceLease(this, ++leaseSerial).also {
            lease = it
            player?.setVideoSurface(surface)
        }
    }
    override fun detachSurface(lease: LibmpvSurfaceLease) = onMain {
        if (this.lease === lease) {
            player?.clearVideoSurface()
            surface = null
            this.lease = null
        }
    }
    internal fun detachSurfaceBeforeDestroy(surface: Surface) = onMain {
        if (this.surface === surface) {
            // SurfaceHolder destruction cannot wait for MediaController's queued
            // command. ExoPlayer synchronously fences the codec's old output here.
            player?.clearVideoSurface(surface)
            this.surface = null
            lease = null
        }
    }
    override fun resizeSurface(lease: LibmpvSurfaceLease, width: Int, height: Int) = onMain {
        if (this.lease === lease && width > 0 && height > 0) {
            surfaceWidth = width
            surfaceHeight = height
        }
    }

    override suspend fun configure(options: PlaybackOptions, resources: LibmpvResourcePaths) = withContext(Dispatchers.Main.immediate) {
        this@ExoPlayerBackend.options = options
        resourcePaths = resources
        ass?.render?.setFontScale(options.subtitleScalePercent / 100f)
        ass?.let(::loadFonts)
        Unit
    }
    private fun loadFonts(handler: AssHandler) {
        resourcePaths.subtitleFontsDirectory?.let { path ->
            File(path).listFiles()?.filter { it.isFile && it.length() in 1..32L * 1024 * 1024 }?.forEach {
                handler.addFont(it.name, it.readBytes())
            }
        }
    }
    private fun readSubtitleBytes(input: InputStream): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return output.toByteArray()
            require(output.size().toLong() + count <= 32L * 1024 * 1024) {
                "Subtitle exceeds the experiment's 32 MiB limit."
            }
            output.write(buffer, 0, count)
        }
    }
    private fun redrawSubtitles(handler: AssHandler) {
        handler.videoTimeCallback?.invoke(handler.videoTime)
    }

    override suspend fun readDiagnostics(): PlaybackDiagnostics = withContext(Dispatchers.Main.immediate) {
        val active = player
        val video = active?.videoFormat
        val audio = active?.audioFormat
        PlaybackDiagnostics(videoCodec = video?.sampleMimeType, audioCodec = audio?.sampleMimeType,
            decoder = decoderName, videoWidth = video?.width?.takeIf { it > 0 },
            videoHeight = video?.height?.takeIf { it > 0 }, sourceFramesPerSecond = video?.frameRate?.toDouble()?.takeIf { it > 0 },
            droppedFrames = droppedFrames, cacheDurationSeconds = active?.let { (it.bufferedPosition - it.currentPosition).coerceAtLeast(0) / 1000.0 },
            colorPrimaries = video?.colorInfo?.colorSpace?.toString(), transferFunction = video?.colorInfo?.colorTransfer?.toString(),
            videoBitrate = video?.bitrate?.toLong()?.takeIf { it > 0 }, audioBitrate = audio?.bitrate?.toLong()?.takeIf { it > 0 },
            engineVersion = "ExoPlayer ${MediaLibraryInfo.VERSION} + libass (ass-media 0.5.1)",
            videoOutput = if (video != null && surface?.isValid == true) "mediacodec-surface" else null,
            audioOutput = audioOffloaded?.let { if (it) "audiotrack-offload" else "audiotrack" },
            audioOffloaded = audioOffloaded)
    }

    private fun trackSnapshot(active: ExoPlayer): TrackSnapshot {
        tracksById.clear()
        val available = mutableListOf<TrackDescriptor>()
        val selected = mutableMapOf<TrackKind, TrackId?>()
        active.currentTracks.groups.forEachIndexed { groupIndex, group ->
            val kind = when (group.type) { C.TRACK_TYPE_VIDEO -> TrackKind.VIDEO; C.TRACK_TYPE_AUDIO -> TrackKind.AUDIO; C.TRACK_TYPE_TEXT -> TrackKind.SUBTITLE; else -> return@forEachIndexed }
            for (index in 0 until group.length) {
                val f = group.getTrackFormat(index)
                val id = TrackId("exo:${group.type}:$groupIndex:$index")
                tracksById[id] = group to index
                available += TrackDescriptor(id, kind, f.language?.takeIf(String::isNotBlank), f.label?.takeIf(String::isNotBlank),
                    f.codecs?.takeIf(String::isNotBlank) ?: f.sampleMimeType, f.bitrate.takeIf { it >= 0 },
                    f.channelCount.takeIf { it > 0 }, f.sampleRate.takeIf { it > 0 }, f.width.takeIf { it > 0 }, f.height.takeIf { it > 0 },
                    roles = buildSet {
                        if (f.selectionFlags and C.SELECTION_FLAG_DEFAULT != 0) add(TrackRole.DEFAULT)
                        if (f.selectionFlags and C.SELECTION_FLAG_FORCED != 0) add(TrackRole.FORCED)
                    }, isExternal = f.id?.contains("ziv-external-") == true,
                    pixelWidthHeightRatio = f.pixelWidthHeightRatio.takeIf { it.isFinite() && it > 0 } ?: 1f,
                    rotationDegrees = ((f.rotationDegrees % 360) + 360) % 360)
                if (group.isTrackSelected(index)) selected[kind] = id
            }
            selected.putIfAbsent(kind, null)
        }
        return TrackSnapshot.of(available, selected)
    }
    private fun position(active: ExoPlayer) = Milliseconds(active.currentPosition.coerceAtLeast(0))
    private fun duration(active: ExoPlayer) = active.duration.takeIf { it >= 0 }?.let(::Milliseconds)

    override suspend fun close() = withContext(Dispatchers.Main.immediate) {
        if (!closed) {
            closed = true
            if (ExoSurfaceOutput.owner === this@ExoPlayerBackend) ExoSurfaceOutput.owner = null
            releasePlayer()
            deleteTemporarySubtitles()
            surface = null
            lease = null
            request = null
            eventChannel.close()
        }
    }
    private fun releasePlayer() {
        main.removeCallbacks(positionPoll)
        seekSerial.incrementAndGet()
        pendingSeek = null
        scrubbing = false
        ExoFrameObservation.positionMs = -1
        ExoSubtitleOutput.clear(this)
        val old = player
        player = null
        audioOffloaded = null
        old?.release()
        ass?.release()
        ass = null
    }
    private fun deleteTemporarySubtitles() {
        temporarySubtitles.forEach(File::delete)
        temporarySubtitles.clear()
    }
    private fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == main.looper) return block()
        val future = FutureTask(block)
        check(main.post(future))
        return future.get()
    }
}
