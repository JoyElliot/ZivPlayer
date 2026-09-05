// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Application
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.data.media.MediaDocumentRegistrar
import io.github.joyelliot.zivplayer.data.media.MediaRepositories
import io.github.joyelliot.zivplayer.data.media.DataStorePlayerPreferences
import io.github.joyelliot.zivplayer.data.media.DocumentTreeLibrary
import io.github.joyelliot.zivplayer.platform.playback.PlaybackHistoryProvider
import io.github.joyelliot.zivplayer.platform.playback.PlaybackResourceLocations
import io.github.joyelliot.zivplayer.platform.playback.ManagedSubtitleResource
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.model.PlaybackOptions
import io.github.joyelliot.zivplayer.core.model.PlaybackResourceId
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.emitAll
import io.github.joyelliot.zivplayer.core.media.PlayerPreferencesRepository
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences

class ZivPlayerApplication : Application(), PlaybackHistoryProvider {
    val mediaRepositories by lazy(LazyThreadSafetyMode.SYNCHRONIZED) { MediaRepositories(this) }
    private val storedPreferences by lazy(LazyThreadSafetyMode.SYNCHRONIZED) { DataStorePlayerPreferences(this) }
    private val maintenanceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val resourceMaintenance by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        maintenanceScope.async(start = CoroutineStart.LAZY) { mediaRepositories.resources.reconcile(storedPreferences) }
    }
    val playerPreferences: PlayerPreferencesRepository = object : PlayerPreferencesRepository {
        override val preferences = flow {
            awaitResourceMaintenance()
            emitAll(storedPreferences.preferences)
        }
        override suspend fun update(transform: (PlayerPreferences) -> PlayerPreferences) {
            awaitResourceMaintenance()
            mediaRepositories.resources.updatePreferences(storedPreferences, transform)
        }
    }
    val documentTreeLibrary by lazy(LazyThreadSafetyMode.SYNCHRONIZED) { DocumentTreeLibrary(this, mediaRepositories) }
    val diagnosticsExporter by lazy(LazyThreadSafetyMode.SYNCHRONIZED) { io.github.joyelliot.zivplayer.data.media.DiagnosticsExporter(this) }
    override val recentMediaRepository: RecentMediaRepository get() = mediaRepositories.recent
    override val playerPreferencesRepository get() = playerPreferences
    override val mediaPlaybackPreferencesRepository get() = mediaRepositories.playbackPreferences

    override fun onCreate() {
        super.onCreate()
        resourceMaintenance.start()
    }

    suspend fun awaitResourceMaintenance() { resourceMaintenance.await() }

    suspend fun deletePlaybackResource(id: PlaybackResourceId) {
        awaitResourceMaintenance()
        mediaRepositories.resources.delete(id, storedPreferences)
    }

    override suspend fun resolvePlaybackResources(options: PlaybackOptions): PlaybackResourceLocations = withContext(Dispatchers.IO) {
        val resources = mediaRepositories.resources
        options.subtitleFont?.let { resources.resolve(it, PlaybackResourceKind.FONT) }
        val fonts = resources.fontsDirectory().apply { check(isDirectory || mkdirs()) }
        PlaybackResourceLocations(fonts.absolutePath, options.shaders.ids.map { resources.resolve(it, PlaybackResourceKind.SHADER).absolutePath })
    }

    override suspend fun importSubtitleResource(uri: Uri): ManagedSubtitleResource {
        val resource = mediaRepositories.resources.import(uri, PlaybackResourceKind.SUBTITLE)
        return ManagedSubtitleResource(resource.id, mediaRepositories.resources.contentUri(resource), resource.title)
    }

    override suspend fun findSubtitleResource(id: PlaybackResourceId): ManagedSubtitleResource? {
        val resource = mediaRepositories.resources.find(id)?.takeIf { it.kind == PlaybackResourceKind.SUBTITLE } ?: return null
        mediaRepositories.resources.resolve(id, PlaybackResourceKind.SUBTITLE)
        return ManagedSubtitleResource(resource.id, mediaRepositories.resources.contentUri(resource), resource.title)
    }

    val mediaDocumentRegistrar: MediaDocumentRegistrar by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        MediaDocumentRegistrar.create(this, recentMediaRepository, mediaRepositories.documentOperations)
    }
}
