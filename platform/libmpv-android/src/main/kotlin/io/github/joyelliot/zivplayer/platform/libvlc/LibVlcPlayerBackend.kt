// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer.platform.libvlc

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
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
    private val surfaceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val channel = Channel<BackendEvent>(Channel.UNLIMITED)
    override val events = channel.receiveAsFlow()
    private var library: LibVLC? = null
    private var player: MediaPlayer? = null
    private var bridge: VlcSeekBridge? = null
    private var mediaFd: ParcelFileDescriptor? = null
    private var request: BackendLoadRequest? = null
    private var prepared = false
    private var initializingSeek = false
    private var initializingPause = false
    private var failed = false
    private var closed = false
    private var scrubbing = false
    private var options = PlaybackOptions()
    private var volume = VolumePercent.FULL
    private var muted = false
    private var rate = PlaybackRatePermille.NORMAL
    private var pendingSeek: BackendSeekRequest? = null
    private var pendingSeekIssued = false
    private var reopenBeforeBackgroundPlay = false
    private var seekEpoch: SeekGeneration? = null
    private var seekToken = 0L
    private var videoSurface: Surface? = null
    private var lease: LibmpvSurfaceLease? = null
    private var leaseSerial = 0L
    private var surfaceWidth = 0
    private var surfaceHeight = 0
    private data class SurfaceRestore(val positionMs: Long, val tracks: Map<Int, IMedia.Track?>)
    private var surfaceRestore: SurfaceRestore? = null
    private var surfaceReopening = false
    private var surfaceReopenScheduled = false
    private var boundVideoSurface: Surface? = null
    private var restoredPausePositionMs: Long? = null
    private var detachedVideoTrackId: String? = null
    private var playAfterSurfaceRestore = false
    private var directVideoOutput = false
    private val tracksById = mutableMapOf<TrackId, IMedia.Track>()
    private val temporarySubtitles = mutableListOf<File>()

    override val configurationMessage: String get() =
        "libVLC 4.0.0-eap29 实验：支持音轨、字幕、倍速及音频/字幕延迟；解码模式在重新打开媒体后生效。mpv 着色器、缩放滤镜、色调映射、导入字体和字幕覆盖样式未接入。"

    private val loadTimeout = Runnable { fail("LibVLC preparation timed out.") }
    private val seekTimeout = Runnable { fail("LibVLC seek completion timed out.") }
    private val positionPoll = object : Runnable {
        override fun run() {
            val active = player ?: return
            val load = request ?: return
            if (!prepared || failed || closed) return
            if (!scrubbing && pendingSeek == null && surfaceRestore == null) channel.trySend(BackendEvent.PositionChanged(
                load.generation, position(active), duration(active), null, seekEpoch))
            main.postDelayed(this, 100)
        }
    }

    override suspend fun load(request: BackendLoadRequest) = serialized { openPlayer(request) }
    private suspend fun openPlayer(request: BackendLoadRequest, restoring: SurfaceRestore? = null) {
        check(!closed)
        val previousEpoch = seekEpoch
        val queuedSeek = pendingSeek
        val queuedPlay = playAfterSurfaceRestore
        releasePlayer(forSurfaceReopen = restoring != null)
        this@LibVlcPlayerBackend.request = request
        prepared = false
        failed = false
        initializingSeek = false
        initializingPause = false
        seekEpoch = if (restoring != null) previousEpoch else null
        surfaceRestore = restoring
        surfaceReopening = restoring != null
        if (restoring != null) { pendingSeek = queuedSeek; playAfterSurfaceRestore = queuedPlay }
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
            // The old player has stopped before this reopen. dup shares its
            // source offset; paused restoration is offered only for seekable media.
            if (restoring != null && uri.scheme == "fd") mediaFd?.let {
                Os.lseek(it.fileDescriptor, 0, OsConstants.SEEK_SET)
            }
        }
        val fd = mediaFd
        val media = if (fd != null) Media(lib, fd.fileDescriptor) else Media(lib, uri)
        try {
            media.setHWDecoderEnabled(options.decoder != DecoderMode.SOFTWARE, false)
            // eap29's AIR/SurfaceControl output loses transforms on this codec.
            // Confirm untransformed local tracks before choosing zero-copy. Keep
            // MediaCodec decoding with picture copy for transformed/unknown media.
            directVideoOutput = options.decoder != DecoderMode.SOFTWARE && hasUntransformedLocalVideo(context, lib, uri)
            if (!directVideoOutput) media.addOption(":no-mediacodec-dr")
            // A fresh paused decoder need not produce a frame or clock. For a
            // resume, preroll muted, seek on the running clock, then acknowledge
            // native pause before exposing READY or restoring audible volume.
            if (restoring == null && request.startPosition.value == 0L) media.addOption(":start-paused")
            if (restoring != null) temporarySubtitles.forEach { file ->
                media.addSlave(IMedia.Slave(IMedia.Slave.Type.Subtitle, 4, Uri.fromFile(file).toString()))
            }
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
            bridge = VlcSeekBridge(active.instance) { token, positionMs ->
                if (player === active && this@LibVlcPlayerBackend.request === request && !closed && !failed)
                    completeSeek(active, token, positionMs)
            }
            bindSurfaces(initializingPlayer = true)
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
                if (initializingPause || (!prepared && !initializingSeek)) publishPrepared(active)
                else if (prepared && !scrubbing) channel.trySend(BackendEvent.PlaybackPaused(load.generation))
            }
            MediaPlayer.Event.Playing -> {
                restoredPausePositionMs = null
                if (!prepared && !initializingSeek && !initializingPause) continuePreparation(active)
                else if (prepared && !scrubbing) channel.trySend(BackendEvent.PlaybackStarted(load.generation))
            }
            MediaPlayer.Event.Buffering -> if (prepared && !scrubbing)
                channel.trySend(BackendEvent.BufferingChanged(load.generation, event.buffering < 100f))
            MediaPlayer.Event.ESAdded, MediaPlayer.Event.ESDeleted, MediaPlayer.Event.ESSelected -> {
                if (prepared) channel.trySend(BackendEvent.TracksChanged(load.generation, trackSnapshot(active)))
                if (surfaceReopening) surfaceRestore?.let { restoreTrackChoices(active, it) }
            }
            MediaPlayer.Event.EncounteredError -> fail("LibVLC could not play this media.")
            // In this v4 binding 0x109 is MediaStopping, despite the Java name EndReached.
            // Wait for Stopped, after error delivery. Our own stop invalidates the player first.
            MediaPlayer.Event.Stopped -> if (prepared)
                channel.trySend(BackendEvent.Ended(load.generation))
                else fail("LibVLC stopped before media preparation completed.")
        }
    }

    private fun continuePreparation(active: MediaPlayer) {
        val load = request ?: return
        if (player !== active || prepared || initializingSeek || failed || closed) return
        val target = surfaceRestore?.positionMs ?: load.startPosition.value
        surfaceRestore?.let { restoreTrackChoices(active, it) }
        if ((target > 0 || surfaceReopening) && active.isSeekable) {
            initializingSeek = true
            runCatching { bridge!!.seek(++seekToken, target, false, requiresSeekOutput(active)) }
                .onFailure { fail("LibVLC resume seek failed.") }
        } else {
            initializingPause = true
            active.pause()
        }
    }

    private fun publishPrepared(active: MediaPlayer) {
        if (prepared || failed) return
        // Explicit choices made while native preparation was in progress win
        // over the tracks selected by the freshly opened media.
        surfaceRestore?.let { restoreTrackChoices(active, it) }
        prepared = true
        initializingSeek = false
        initializingPause = false
        main.removeCallbacks(loadTimeout)
        applyAudio(active)
        if (surfaceReopening) {
            restoredPausePositionMs = surfaceRestore?.positionMs
            surfaceRestore = null
            surfaceReopening = false
            val generation = checkNotNull(request).generation
            channel.trySend(BackendEvent.TracksChanged(generation, trackSnapshot(active)))
            channel.trySend(BackendEvent.BufferingChanged(generation, false))
            main.post(positionPoll)
            pendingSeek?.let { issueSeek(it); return }
            if (playAfterSurfaceRestore) { playAfterSurfaceRestore = false; active.play() }
            return
        }
        val capabilities = mutableSetOf(PlayerCapability.PLAY, PlayerCapability.PAUSE, PlayerCapability.STOP,
            PlayerCapability.SET_VOLUME, PlayerCapability.SET_MUTED, PlayerCapability.SET_RATE,
            PlayerCapability.SELECT_TRACK, PlayerCapability.ADD_SUBTITLE)
        if (active.isSeekable) capabilities += PlayerCapability.SEEK
        channel.trySend(BackendEvent.Prepared(checkNotNull(request).generation, duration(active),
            trackSnapshot(active), PlayerCapabilities.of(capabilities)))
        main.post(positionPoll)
    }

    private fun completeSeek(active: MediaPlayer, token: Long, positionMs: Long) {
        if (token != seekToken) return
        if (initializingSeek) {
            initializingPause = true
            active.pause()
            return
        }
        val pending = pendingSeek ?: return
        restoredPausePositionMs = null
        pendingSeek = null
        pendingSeekIssued = false
        if (!surfaceReopening) surfaceRestore = surfaceRestore?.copy(positionMs = positionMs)
        main.removeCallbacks(seekTimeout)
        channel.trySend(BackendEvent.SeekCompleted(pending.generation, pending.seekGeneration, Milliseconds(positionMs)))
        if (playAfterSurfaceRestore) { playAfterSurfaceRestore = false; active.play() }
    }

    override suspend fun play() = serialized {
        endScrubbing()
        restoredPausePositionMs = null
        if (surfaceRestore != null && !surfaceReopening && videoSurface == null) {
            if (reopenBeforeBackgroundPlay) {
                // eap29 can advance its PCR by seconds when a detached paused
                // pipeline resumes after a no-output seek. Keep its acknowledged
                // cursor, but initialize fresh clocks before background playback.
                val restore = checkNotNull(surfaceRestore)
                val load = checkNotNull(request)
                playAfterSurfaceRestore = true
                surfaceReopening = true
                channel.trySend(BackendEvent.BufferingChanged(load.generation, true))
                openPlayer(load, restore)
                return@serialized
            }
            // Notification play while the screen is absent must still resume
            // audio. The attached window will select the saved video track.
            surfaceRestore = null
            if (pendingSeek != null) {
                playAfterSurfaceRestore = true
                if (!pendingSeekIssued) issueSeek(checkNotNull(pendingSeek))
            }
            else checkNotNull(player).play()
        } else if (surfaceRestore != null) playAfterSurfaceRestore = true else checkNotNull(player).play()
    }
    override suspend fun pause() = serialized {
        // This Java binding calls deterministic libvlc_media_player_set_pause(..., 1).
        playAfterSurfaceRestore = false
        if (prepared) player?.pause()
        Unit
    }
    override suspend fun seekTo(request: BackendSeekRequest) = serialized {
        check(this@LibVlcPlayerBackend.request?.generation == request.generation && (prepared || surfaceReopening) && !failed)
        check(pendingSeek == null) // Core coalesces previews while the native seek is unfinished.
        if (request.precision == SeekPrecision.EXACT) endScrubbing() else beginScrubbing()
        pendingSeek = request
        pendingSeekIssued = false
        restoredPausePositionMs = null
        seekEpoch = request.seekGeneration
        // The native watcher accepts one request. A user seek during restoration
        // waits for its completion, then replaces the restored position precisely.
        if (surfaceRestore == null || (!surfaceReopening && videoSurface?.isValid != true)) {
            // With no window, acknowledge the native logical cursor now. Keep
            // that position for a later video reopen instead of leaving a user
            // seek pending until a Surface arrives (the core has an 8s timeout).
            surfaceRestore = surfaceRestore?.copy(positionMs = request.position.value)
            reopenBeforeBackgroundPlay = surfaceRestore != null
            issueSeek(request)
        }
        Unit
    }
    private fun issueSeek(request: BackendSeekRequest) {
        checkNotNull(bridge).seek(++seekToken, request.position.value, request.precision == SeekPrecision.KEYFRAME,
            requiresSeekOutput(checkNotNull(player)))
        pendingSeekIssued = true
        main.postDelayed(seekTimeout, 8_000)
    }
    private fun requiresSeekOutput(active: MediaPlayer) = active.isPlaying ||
        (videoSurface?.isValid == true && active.getSelectedTrack(IMedia.Track.Type.Video) != null)
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
        val track = trackId?.let { checkNotNull(tracksById[it]) { "Unavailable VLC track." } }
        require(track == null || track.type == type)
        surfaceRestore?.let { restore ->
            surfaceRestore = restore.copy(tracks = restore.tracks + (type to track))
            // The old Java descriptors remain valid metadata while the new
            // player discovers ES IDs. Paused/ES events apply this latest intent.
            if (surfaceReopening) return@serialized
        }
        if (track == null) active.unselectTrackType(type)
        else check(active.selectTrack(track.id)) { "VLC track selection failed." }
        channel.trySend(BackendEvent.TracksChanged(checkNotNull(request).generation, trackSnapshot(active)))
        Unit
    }

    override suspend fun addSubtitle(source: SubtitleSource) = serialized {
        check(!closed && player != null)
        val active = checkNotNull(player)
        val previousIds = active.getTracks(IMedia.Track.Type.Text)?.map { it.id }?.toSet().orEmpty()
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
            check(active.addSlave(IMedia.Slave.Type.Subtitle, Uri.fromFile(file), true)) {
                "LibVLC could not add the subtitle."
            }
            temporarySubtitles += file
            copiedFile = null // Ownership transferred to releasePlayer, including cancellation paths.
            // eap29 addSlave(select=true) adds the ES but does not override a
            // previously disabled subtitle category. Select its actual new ID.
            val added = withTimeout(5_000) {
                var track = active.getTracks(IMedia.Track.Type.Text)?.firstOrNull { it.id !in previousIds }
                while (track == null) {
                    delay(20)
                    track = active.getTracks(IMedia.Track.Type.Text)?.firstOrNull { it.id !in previousIds }
                }
                track
            }
            surfaceRestore?.let { restore ->
                surfaceRestore = restore.copy(tracks = restore.tracks + (IMedia.Track.Type.Text to added))
            }
            check(active.selectTrack(added.id)) { "LibVLC could not select the added subtitle." }
            if (prepared) channel.trySend(BackendEvent.TracksChanged(checkNotNull(request).generation, trackSnapshot(active)))
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
        player?.getSelectedTrack(IMedia.Track.Type.Video)?.id?.let { detachedVideoTrackId = it }
        preservePausedSurfacePosition()
        player?.vlcVout?.detachViews()
        boundVideoSurface = null
        videoSurface = null
        lease = null
    }
    private fun preservePausedSurfacePosition() {
        val active = player ?: return
        if (surfaceRestore != null || !prepared || failed || pendingSeek != null || active.isPlaying || !active.isSeekable) return
        val video = active.getSelectedTrack(IMedia.Track.Type.Video) ?: detachedVideoTrackId?.let { id ->
            active.getTracks(IMedia.Track.Type.Video)?.firstOrNull { it.id == id }
        } ?: return
        // eap29 detaching AWindow disables video and destroys its decoder. For
        // paused video-only media the native clock can then reset to an old time.
        surfaceRestore = SurfaceRestore(position(active).value,
            listOf(IMedia.Track.Type.Video, IMedia.Track.Type.Audio, IMedia.Track.Type.Text)
                .associateWith { if (it == IMedia.Track.Type.Video) video else active.getSelectedTrack(it) })
    }
    private fun restoreTrackChoices(active: MediaPlayer, restore: SurfaceRestore) {
        restore.tracks.forEach { (type, saved) ->
            val selected = active.getSelectedTrack(type)
            if (saved == null) {
                if (selected != null) active.unselectTrackType(type)
            } else if (selected?.id != saved.id) {
                val match = active.getTracks(type)?.firstOrNull { it.id == saved.id }
                if (match != null) active.selectTrack(match.id)
            }
        }
    }
    private fun applySurfaceGeometry(active: MediaPlayer) {
        if (surfaceWidth <= 0 || surfaceHeight <= 0) return
        val video = active.getSelectedTrack(IMedia.Track.Type.Video) as? IMedia.VideoTrack
        // VLC applies this DAR before the source's rotation. Opaque decoder
        // output may already be normalized; copy output still has its transform.
        val swapsAxes = video?.orientation?.and(4) != 0 && video != null
        active.setAspectRatio(if (swapsAxes) "$surfaceHeight:$surfaceWidth" else "$surfaceWidth:$surfaceHeight")
        active.vlcVout.setWindowSize(surfaceWidth, surfaceHeight)
    }
    internal fun bindSurfaces(initializingPlayer: Boolean = false): Unit = onMain {
        val active = player ?: return@onMain
        val video = videoSurface?.takeIf(Surface::isValid) ?: return@onMain
        if (boundVideoSurface === video && active.vlcVout.areViewsAttached()) {
            applySurfaceGeometry(active)
            return@onMain
        }
        if (!initializingPlayer && boundVideoSurface !== video) preservePausedSurfacePosition()
        if (!initializingPlayer && surfaceRestore != null && boundVideoSurface !== video) {
            if (!surfaceReopenScheduled) {
                surfaceReopenScheduled = true
                val load = checkNotNull(request)
                surfaceScope.launch {
                    try {
                        serialized {
                            // A replacement Surface may arrive while stop runs
                            // on IO. openPlayer binds the latest valid Surface.
                            val restore = surfaceRestore
                            if (!closed && !failed && request === load && restore != null && videoSurface?.isValid == true) {
                                // A transient replacement can disappear while
                                // this job waits for the lifecycle lock. Only an
                                // actual reopen enters the restoring state.
                                surfaceReopening = true
                                channel.trySend(BackendEvent.BufferingChanged(load.generation, true))
                                runCatching { openPlayer(load, restore) }
                                    .onFailure { fail("LibVLC could not reopen its paused video surface.") }
                            }
                        }
                    } finally {
                        surfaceReopenScheduled = false
                        if (!closed && !failed && surfaceRestore != null && videoSurface?.isValid == true &&
                            boundVideoSurface !== videoSurface) bindSurfaces()
                    }
                }
            }
            return@onMain
        }
        val vout = active.vlcVout
        if (vout.areViewsAttached()) { preservePausedSurfacePosition(); vout.detachViews() }
        vout.setVideoSurface(video, null)
        val binding = VlcSurfaceOutput.bindingFor(video)
        // Use the View API: eap29 marks two bare Surface helpers ready before
        // the second attach, then throws on its duplicate ready notification.
        binding?.let { vout.setSubtitlesView(it.subtitles) }
        applySurfaceGeometry(active)
        // A layout listener plus two Surfaces allows MediaCodec's android-display
        // output. VLC retains its GLES fallback for unsupported/direct-less modes.
        if (binding == null) vout.attachViews()
        else vout.attachViews { _, displayWidth, displayHeight, placeWidth, placeHeight, placeX, placeY ->
            if (player === active && videoSurface === video) {
                binding.onLayout(displayWidth, displayHeight, placeWidth, placeHeight, placeX, placeY)
            }
        }
        boundVideoSurface = video
        if (surfaceRestore == null) detachedVideoTrackId?.let { id ->
            if (active.getSelectedTrack(IMedia.Track.Type.Video)?.id != id) active.selectTrack(id)
            detachedVideoTrackId = null
        }
    }
    override fun resizeSurface(lease: LibmpvSurfaceLease, width: Int, height: Int) = onMain {
        if (this.lease === lease && width > 0 && height > 0) {
            surfaceWidth = width
            surfaceHeight = height
            // The UI already chooses the FIT/FILL/STRETCH rectangle. Native
            // SurfaceControl children must fill that actual window rectangle.
            player?.let(::applySurfaceGeometry)
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
                    rotationDegrees = when (video?.orientation) { 3 -> 180; 5 -> 270; 4, 6, 7 -> 90; else -> 0 })
                if (track.selected) selected[kind] = id
            }
        }
        return TrackSnapshot.of(available, selected)
    }
    private fun position(active: MediaPlayer) = Milliseconds(surfaceRestore?.positionMs ?:
        restoredPausePositionMs?.takeIf { !active.isPlaying } ?: bridge?.positionMs() ?: active.time.coerceAtLeast(0))
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
    private suspend fun releasePlayer(forSurfaceReopen: Boolean = false) = withContext(NonCancellable + Dispatchers.Main.immediate) {
        main.removeCallbacksAndMessages(null)
        val old = player
        val oldBridge = bridge
        val oldFd = mediaFd
        val oldSubtitles = if (forSurfaceReopen) emptyList() else temporarySubtitles.toList()
        player = null // Invalidates all old event/seek callbacks before stop can send Stopped.
        bridge = null
        mediaFd = null
        if (!forSurfaceReopen) temporarySubtitles.clear()
        prepared = false
        initializingSeek = false
        initializingPause = false
        pendingSeek = null
        pendingSeekIssued = false
        reopenBeforeBackgroundPlay = false
        surfaceRestore = null
        surfaceReopening = false
        boundVideoSurface = null
        restoredPausePositionMs = null
        detachedVideoTrackId = null
        playAfterSurfaceRestore = false
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
        if (!forSurfaceReopen) tracksById.clear()
        cleanupFailure?.let { throw it }
    }
    override suspend fun close() = withContext(NonCancellable) { serialized {
        if (!closed) {
            closed = true
            surfaceScope.cancel()
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
