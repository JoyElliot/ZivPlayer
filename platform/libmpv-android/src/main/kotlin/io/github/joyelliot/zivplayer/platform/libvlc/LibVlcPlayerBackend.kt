// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.libvlc

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.view.Surface
import io.github.joyelliot.zivplayer.core.model.*
import io.github.joyelliot.zivplayer.core.player.*
import io.github.joyelliot.zivplayer.core.player.runtime.*
import io.github.joyelliot.zivplayer.platform.exoplayer.DirectScrubPort
import io.github.joyelliot.zivplayer.platform.libmpv.*
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.util.concurrent.FutureTask
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.videolan.libvlc.LibVLC
import org.videolan.libvlc.Media
import org.videolan.libvlc.MediaPlayer
import org.videolan.libvlc.interfaces.IMedia

/** Experimental backend. Queue, playback intent, audio focus and history remain in the core/service. */
class LibVlcPlayerBackend(context: Context) : PlayerBackend, LibmpvSurfacePort,
    LibmpvConfigurationPort, DirectScrubPort {
    private val context = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val lifecycle = Mutex()
    private val channel = Channel<BackendEvent>(Channel.UNLIMITED)
    override val events = channel.receiveAsFlow()
    private var library: LibVLC? = null
    private var player: MediaPlayer? = null
    private var bridge: VlcSeekBridge? = null
    private var mediaFd: ParcelFileDescriptor? = null
    private var request: BackendLoadRequest? = null
    private var prepared = false
    private var initializingSeek = false
    private var failed = false
    private var closed = false
    private var scrubbing = false
    private var options = PlaybackOptions()
    private var volume = VolumePercent.FULL
    private var muted = false
    private var rate = PlaybackRatePermille.NORMAL
    private var pendingSeek: BackendSeekRequest? = null
    private var seekEpoch: SeekGeneration? = null
    private var seekToken = 0L
    private var videoSurface: Surface? = null
    private var lease: LibmpvSurfaceLease? = null
    private var leaseSerial = 0L
    private var surfaceWidth = 0
    private var surfaceHeight = 0
    private val tracksById = mutableMapOf<TrackId, IMedia.Track>()
    private val temporarySubtitles = mutableListOf<File>()

    override val configurationMessage: String get() =
        "libVLC 4.0.0-eap29 实验（OpenGL ES 合成）：支持音轨、字幕、倍速及音频/字幕延迟；解码模式在重新打开媒体后生效。mpv 着色器、缩放滤镜、色调映射、导入字体和字幕覆盖样式未接入。"

    private val loadTimeout = Runnable { fail("LibVLC preparation timed out.") }
    private val seekTimeout = Runnable { fail("LibVLC seek completion timed out.") }
    private val positionPoll = object : Runnable {
        override fun run() {
            val active = player ?: return
            val load = request ?: return
            if (!prepared || failed || closed) return
            if (!scrubbing && pendingSeek == null) channel.trySend(BackendEvent.PositionChanged(
                load.generation, position(active), duration(active), null, seekEpoch))
            main.postDelayed(this, 100)
        }
    }

    override suspend fun load(request: BackendLoadRequest) = serialized {
        check(!closed)
        releasePlayer()
        this@LibVlcPlayerBackend.request = request
        prepared = false
        failed = false
        initializingSeek = false
        seekEpoch = null
        val lib = library ?: LibVLC(context, arrayListOf("--no-video-title-show")).also { library = it }
        val source = request.item.media.source
        val uri = Uri.parse(source.locator)
        try {
        withContext(Dispatchers.IO) {
            // Publish ownership inside IO so cancellation during return cannot lose an opened FD.
            mediaFd = when (uri.scheme) {
                "content" -> checkNotNull(context.contentResolver.openFileDescriptor(uri, "r"))
                "fd" -> ParcelFileDescriptor.fromFd(source.locator.removePrefix("fd://").toInt())
                else -> null
            }
        }
        val fd = mediaFd
        val media = if (fd != null) Media(lib, fd.fileDescriptor) else Media(lib, uri)
        try {
            media.setHWDecoderEnabled(options.decoder != DecoderMode.SOFTWARE, false)
            media.addOption(":start-paused")
            // This wrapper exposes no generic HTTP-header API. Never silently lose credentials.
            source.httpHeaders.forEach { (name, value) ->
                require(value.none { it == '\r' || it == '\n' })
                media.addOption(when (name.lowercase()) {
                    "user-agent" -> ":http-user-agent=$value"
                    "referer", "referrer" -> ":http-referrer=$value"
                    else -> error("LibVLC experiment does not support this HTTP header: $name")
                })
            }
            val active = MediaPlayer(lib)
            player = active
            active.media = media
            active.volume = 0 // Preparation never emits audio before core playback intent permits it.
            active.rate = rate.value / 1000f
            active.setEventListener { event ->
                if (player === active && this@LibVlcPlayerBackend.request === request && !closed && !failed)
                    handleEvent(active, event)
            }
            bridge = VlcSeekBridge(active.instance) { token ->
                if (player === active && this@LibVlcPlayerBackend.request === request && !closed && !failed)
                    completeSeek(active, token)
            }
            bindSurfaces()
            main.postDelayed(loadTimeout, 30_000)
            active.play()
        } finally { media.release() }
        } catch (error: Throwable) {
            runCatching { releasePlayer() }.exceptionOrNull()?.let(error::addSuppressed)
            throw error
        }
    }

    private fun handleEvent(active: MediaPlayer, event: MediaPlayer.Event) {
        val load = request ?: return
        when (event.type) {
            MediaPlayer.Event.Paused -> {
                if (!prepared && !initializingSeek) {
                    if (load.startPosition.value > 0 && active.isSeekable) {
                        initializingSeek = true
                        runCatching { bridge!!.seek(++seekToken, load.startPosition.value, false) }
                            .onFailure { fail("LibVLC resume seek failed.") }
                    } else publishPrepared(active)
                } else if (prepared && !scrubbing) channel.trySend(BackendEvent.PlaybackPaused(load.generation))
            }
            MediaPlayer.Event.Playing -> if (prepared && !scrubbing)
                channel.trySend(BackendEvent.PlaybackStarted(load.generation))
            MediaPlayer.Event.Buffering -> if (prepared && !scrubbing)
                channel.trySend(BackendEvent.BufferingChanged(load.generation, event.buffering < 100f))
            MediaPlayer.Event.ESAdded, MediaPlayer.Event.ESDeleted, MediaPlayer.Event.ESSelected ->
                if (prepared) channel.trySend(BackendEvent.TracksChanged(load.generation, trackSnapshot(active)))
            MediaPlayer.Event.EncounteredError -> fail("LibVLC could not play this media.")
            // In this v4 binding 0x109 is MediaStopping, despite the Java name EndReached.
            // Wait for Stopped, after error delivery. Our own stop invalidates the player first.
            MediaPlayer.Event.Stopped -> if (prepared)
                channel.trySend(BackendEvent.Ended(load.generation))
                else fail("LibVLC stopped before media preparation completed.")
        }
    }

    private fun publishPrepared(active: MediaPlayer) {
        if (prepared || failed) return
        prepared = true
        initializingSeek = false
        main.removeCallbacks(loadTimeout)
        applyAudio(active)
        val capabilities = mutableSetOf(PlayerCapability.PLAY, PlayerCapability.PAUSE, PlayerCapability.STOP,
            PlayerCapability.SET_VOLUME, PlayerCapability.SET_MUTED, PlayerCapability.SET_RATE,
            PlayerCapability.SELECT_TRACK, PlayerCapability.ADD_SUBTITLE)
        if (active.isSeekable) capabilities += PlayerCapability.SEEK
        channel.trySend(BackendEvent.Prepared(checkNotNull(request).generation, duration(active),
            trackSnapshot(active), PlayerCapabilities.of(capabilities)))
        main.post(positionPoll)
    }

    private fun completeSeek(active: MediaPlayer, token: Long) {
        if (token != seekToken) return
        if (initializingSeek) { publishPrepared(active); return }
        val pending = pendingSeek ?: return
        pendingSeek = null
        main.removeCallbacks(seekTimeout)
        channel.trySend(BackendEvent.SeekCompleted(pending.generation, pending.seekGeneration, position(active)))
    }

    override suspend fun play() = serialized {
        endScrubbing()
        checkNotNull(player).play()
    }
    override suspend fun pause() = serialized {
        // This Java binding calls deterministic libvlc_media_player_set_pause(..., 1).
        player?.pause()
        Unit
    }
    override suspend fun seekTo(request: BackendSeekRequest) = serialized {
        check(this@LibVlcPlayerBackend.request?.generation == request.generation && prepared && !failed)
        check(pendingSeek == null) // Core coalesces previews while the native seek is unfinished.
        if (request.precision == SeekPrecision.EXACT) endScrubbing() else beginScrubbing()
        pendingSeek = request
        seekEpoch = request.seekGeneration
        checkNotNull(bridge).seek(++seekToken, request.position.value, request.precision == SeekPrecision.KEYFRAME)
        main.postDelayed(seekTimeout, 8_000)
        Unit
    }
    override fun beginScrubbing() = onMain { scrubbing = true }
    override fun endScrubbing() = onMain { scrubbing = false }
    override suspend fun stop() = serialized {
        releasePlayer()
        request = null
    }
    override suspend fun setVolume(volume: VolumePercent) = serialized {
        this@LibVlcPlayerBackend.volume = volume
        if (prepared) player?.let(::applyAudio)
        Unit
    }
    override suspend fun setMuted(muted: Boolean) = serialized {
        this@LibVlcPlayerBackend.muted = muted
        if (prepared) player?.let(::applyAudio)
        Unit
    }
    override suspend fun setPlaybackRate(playbackRate: PlaybackRatePermille) = serialized {
        rate = playbackRate
        player?.rate = rate.value / 1000f
    }
    private fun applyAudio(active: MediaPlayer) {
        active.volume = if (muted) 0 else volume.value
        active.setAudioDelay(options.audioDelayMs * 1_000L)
        active.setSpuDelay(options.subtitleDelayMs * 1_000L)
    }
    override suspend fun selectTrack(kind: TrackKind, trackId: TrackId?) = serialized {
        val active = checkNotNull(player)
        val type = nativeType(kind)
        if (trackId == null) active.unselectTrackType(type)
        else {
            val track = checkNotNull(tracksById[trackId]) { "Unavailable VLC track." }
            check(track.type == type && active.selectTrack(track.id)) { "VLC track selection failed." }
        }
        channel.trySend(BackendEvent.TracksChanged(checkNotNull(request).generation, trackSnapshot(active)))
        Unit
    }

    override suspend fun addSubtitle(source: SubtitleSource) = serialized {
        check(!closed && player != null)
        var copiedFile: File? = null
        try {
        val file = withContext(Dispatchers.IO) {
            val bytes = if (source.locator.startsWith("fd://")) {
                ParcelFileDescriptor.AutoCloseInputStream(ParcelFileDescriptor.fromFd(
                    source.locator.removePrefix("fd://").toInt())).use(::readSubtitle)
            } else checkNotNull(context.contentResolver.openInputStream(Uri.parse(source.locator))).use(::readSubtitle)
            val ass = bytes.take(2048).toByteArray().toString(Charsets.UTF_8).contains("[Script Info]", true)
            File.createTempFile("ziv-vlc-sub-", if (ass) ".ass" else ".srt", context.cacheDir).also {
                copiedFile = it
                it.writeBytes(bytes)
            }
        }
            check(checkNotNull(player).addSlave(IMedia.Slave.Type.Subtitle, Uri.fromFile(file), true)) {
                "LibVLC could not add the subtitle."
            }
            temporarySubtitles += file
            copiedFile = null // Ownership transferred to releasePlayer, including cancellation paths.
        } finally {
            withContext(NonCancellable + Dispatchers.IO) { copiedFile?.delete() }
        }
        Unit
    }
    private fun readSubtitle(input: InputStream): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return output.toByteArray()
            require(output.size().toLong() + count <= 32L * 1024 * 1024) { "Subtitle exceeds the 32 MiB experiment limit." }
            output.write(buffer, 0, count)
        }
    }

    override fun attachSurface(surface: Surface): LibmpvSurfaceLease = onMain {
        check(!closed && surface.isValid)
        videoSurface = surface
        VlcSurfaceOutput.owner = this
        LibmpvSurfaceLease(this, ++leaseSerial).also { lease = it; bindSurfaces() }
    }
    override fun detachSurface(lease: LibmpvSurfaceLease) = onMain {
        if (this.lease === lease) detachVideoSurface()
    }
    internal fun beforeSurfaceDestroyed(surface: Surface) = onMain {
        if (videoSurface === surface) detachVideoSurface()
    }
    private fun detachVideoSurface() {
        player?.vlcVout?.detachViews()
        videoSurface = null
        lease = null
    }
    internal fun bindSurfaces() = onMain {
        val active = player ?: return@onMain
        val video = videoSurface?.takeIf(Surface::isValid) ?: return@onMain
        val vout = active.vlcVout
        if (vout.areViewsAttached()) vout.detachViews()
        vout.setVideoSurface(video, null)
        if (surfaceWidth > 0 && surfaceHeight > 0) vout.setWindowSize(surfaceWidth, surfaceHeight)
        // A null layout listener selects VLC's GLES compositor, including subtitles.
        // VideoViewport supplies a display-aspect buffer and owns FIT/FILL/STRETCH.
        vout.attachViews()
    }
    override fun resizeSurface(lease: LibmpvSurfaceLease, width: Int, height: Int) = onMain {
        if (this.lease === lease && width > 0 && height > 0) {
            surfaceWidth = width
            surfaceHeight = height
            player?.vlcVout?.setWindowSize(width, height)
        }
    }
    override suspend fun configure(options: PlaybackOptions, resources: LibmpvResourcePaths) = serialized {
        this@LibVlcPlayerBackend.options = options
        if (prepared) player?.let(::applyAudio)
        Unit
    }
    override suspend fun readDiagnostics(): PlaybackDiagnostics = withContext(Dispatchers.Main.immediate) {
        val active = player
        val video = active?.getSelectedTrack(IMedia.Track.Type.Video) as? IMedia.VideoTrack
        val audio = active?.getSelectedTrack(IMedia.Track.Type.Audio) as? IMedia.AudioTrack
        val media = active?.media
        val stats = try { media?.stats } finally { media?.release() }
        PlaybackDiagnostics(videoCodec = video?.codec, audioCodec = audio?.codec,
            videoWidth = video?.width?.takeIf { it > 0 }, videoHeight = video?.height?.takeIf { it > 0 },
            sourceFramesPerSecond = video?.takeIf { it.frameRateDen > 0 && it.frameRateNum > 0 }
                ?.let { it.frameRateNum.toDouble() / it.frameRateDen },
            droppedFrames = stats?.lostPictures?.takeIf { it >= 0 },
            videoBitrate = video?.bitrate?.toLong()?.takeIf { it > 0 }, audioBitrate = audio?.bitrate?.toLong()?.takeIf { it > 0 },
            engineVersion = "libVLC ${LibVLC.version()} (Android 4.0.0-eap29)")
        // Java does not expose the active decoder name. Leave it null rather than report requested mode as proof.
    }

    private fun nativeType(kind: TrackKind) = when (kind) {
        TrackKind.VIDEO -> IMedia.Track.Type.Video
        TrackKind.AUDIO -> IMedia.Track.Type.Audio
        TrackKind.SUBTITLE -> IMedia.Track.Type.Text
    }
    private fun trackSnapshot(active: MediaPlayer): TrackSnapshot {
        tracksById.clear()
        val available = mutableListOf<TrackDescriptor>()
        val selected = mutableMapOf<TrackKind, TrackId?>()
        TrackKind.entries.forEach { kind ->
            selected[kind] = null
            active.getTracks(nativeType(kind))?.forEach { track ->
                val id = TrackId("vlc:${track.type}:${track.id}")
                tracksById[id] = track
                val video = track as? IMedia.VideoTrack
                val audio = track as? IMedia.AudioTrack
                available += TrackDescriptor(id, kind, languageTag = track.language?.takeIf(String::isNotBlank),
                    label = track.name?.takeIf(String::isNotBlank) ?: track.description?.takeIf(String::isNotBlank),
                    codec = track.codec?.takeIf(String::isNotBlank), bitrate = track.bitrate.takeIf { it > 0 },
                    channels = audio?.channels?.takeIf { it > 0 }, sampleRateHz = audio?.rate?.takeIf { it > 0 },
                    width = video?.width?.takeIf { it > 0 }, height = video?.height?.takeIf { it > 0 },
                    pixelWidthHeightRatio = video?.takeIf { it.sarNum > 0 && it.sarDen > 0 }?.let { it.sarNum.toFloat() / it.sarDen } ?: 1f,
                    rotationDegrees = when (video?.orientation) { 3 -> 180; 5 -> 90; 6 -> 270; else -> 0 })
                if (track.selected) selected[kind] = id
            }
        }
        return TrackSnapshot.of(available, selected)
    }
    private fun position(active: MediaPlayer) = Milliseconds(active.time.coerceAtLeast(0))
    private fun duration(active: MediaPlayer) = active.length.takeIf { it > 0 }?.let(::Milliseconds)
    private fun fail(message: String) {
        val load = request ?: return
        if (failed || closed) return
        failed = true
        main.removeCallbacks(positionPoll)
        main.removeCallbacks(loadTimeout)
        main.removeCallbacks(seekTimeout)
        runCatching { player?.pause() } // Terminal failure must not leave audio/video advancing.
        channel.trySend(BackendEvent.Failure(load.generation,
            PlayerError(PlayerErrorKind.BACKEND_OPERATION_FAILED, message, ErrorRecovery.RESET)))
    }
    private suspend fun releasePlayer() = withContext(NonCancellable + Dispatchers.Main.immediate) {
        main.removeCallbacksAndMessages(null)
        val old = player
        val oldBridge = bridge
        val oldFd = mediaFd
        val oldSubtitles = temporarySubtitles.toList()
        player = null // Invalidates all old event/seek callbacks before stop can send Stopped.
        bridge = null
        mediaFd = null
        temporarySubtitles.clear()
        prepared = false
        initializingSeek = false
        pendingSeek = null
        scrubbing = false
        var cleanupFailure: Throwable? = null
        fun clean(action: () -> Unit) {
            runCatching(action).exceptionOrNull()?.let { error ->
                val first = cleanupFailure
                if (first == null) cleanupFailure = error else first.addSuppressed(error)
            }
        }
        clean { oldBridge?.close() }
        clean { old?.setEventListener(null) }
        clean { old?.vlcVout?.detachViews() }
        // eap29 Java stop blocks until native Stopped; never perform it on the UI thread.
        withContext(Dispatchers.IO) { clean { old?.stop() } }
        clean { old?.release() } // AWindow detachment in release requires the main thread.
        withContext(Dispatchers.IO) {
            clean { oldFd?.close() }
            oldSubtitles.forEach { file -> clean { file.delete(); Unit } }
        }
        tracksById.clear()
        cleanupFailure?.let { throw it }
    }
    override suspend fun close() = withContext(NonCancellable) { serialized {
        if (!closed) {
            closed = true
            try { releasePlayer() } finally {
                val oldLibrary = library
                library = null
                if (VlcSurfaceOutput.owner === this@LibVlcPlayerBackend) VlcSurfaceOutput.owner = null
                videoSurface = null
                lease = null
                request = null
                channel.close()
                oldLibrary?.release()
            }
        }
    } }
    private suspend fun <T> serialized(block: suspend () -> T): T = lifecycle.withLock {
        withContext(Dispatchers.Main.immediate) { block() }
    }
    private fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == main.looper) return block()
        val future = FutureTask(block)
        check(main.post(future))
        return future.get()
    }
}
