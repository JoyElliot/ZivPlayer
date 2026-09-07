// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.Context
import android.net.Uri
import android.os.CancellationSignal
import android.provider.OpenableColumns
import androidx.room.withTransaction
import io.github.joyelliot.zivplayer.core.media.PlaybackResource
import io.github.joyelliot.zivplayer.core.media.PlaybackResourceKind
import io.github.joyelliot.zivplayer.core.media.PlayerPreferencesRepository
import io.github.joyelliot.zivplayer.core.model.PlaybackResourceId
import io.github.joyelliot.zivplayer.core.model.PlayerPreferences
import io.github.joyelliot.zivplayer.core.model.ShaderChain
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import java.util.concurrent.atomic.AtomicReference

/** Imports are content-addressed and remain readable after a provider grant expires. */
class ManagedPlaybackResources internal constructor(
    context: Context,
    private val database: ZivMediaDatabase,
) {
    private val app = context.applicationContext
    private val root = File(app.filesDir, "playback-resources")
    private val operations = Mutex()
    private val providerCopies = BoundedBlockingIo("ZivResourceProvider")
    private val dao = database.playbackResourcesDao()
    val resources = dao.observe().map { entries -> entries.mapNotNull { runCatching { it.model() }.getOrNull() } }

    suspend fun import(uri: Uri, kind: PlaybackResourceKind): PlaybackResource = withContext(Dispatchers.IO) {
        val title = withTimeout(15_000) { providerCopies.run { _ ->
            app.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                ?.use { if (it.moveToFirst()) it.getString(0) else null } ?: uri.lastPathSegment.orEmpty()
        } }
        import(uri, title, kind)
    }

    suspend fun import(uri: Uri, title: String, kind: PlaybackResourceKind): PlaybackResource =
        withContext(Dispatchers.IO) {
            operations.withLock {
                reconcileFiles()
                require(uri.scheme == "content") { "Choose a file using the document picker." }
                val safeTitle = title.filterNot(Char::isISOControl).take(240).ifBlank { kind.name.lowercase() }
                val extension = safeTitle.substringAfterLast('.', "").lowercase()
                require(extension in extensions(kind)) { "Unsupported ${kind.name.lowercase()} file." }
                val current = dao.all()
                require(current.size < MAX_RESOURCES) { "Remove an imported file before adding another." }
                val limit = limitBytes(kind)
                require(current.sumOf { it.sizeBytes } + limit <= MAX_TOTAL_BYTES) { "Imported files have reached the storage limit." }
                val folder = directory(kind).apply { check(isDirectory || mkdirs()) }
                val temp = File(folder, ".import-${UUID.randomUUID()}")
                try {
                    withTimeout(30_000) { copy(uri, temp, limit) }
                    val bytes = temp.readBytes()
                    val family = if (kind == PlaybackResourceKind.FONT) FontMetadata.family(bytes) else null
                    if (kind == PlaybackResourceKind.SHADER) validateShader(bytes)
                    if (kind == PlaybackResourceKind.SUBTITLE) require(bytes.none { it == 0.toByte() }) {
                        "Use a text subtitle file (SRT, ASS, SSA, VTT or SUB)."
                    }
                    val digest = MessageDigest.getInstance("SHA-256")
                    digest.update(kind.name.toByteArray(Charsets.US_ASCII))
                    val id = digest.digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
                    dao.find(id)?.let { existing ->
                        if (file(existing.model()).isFile) return@withLock existing.model()
                    }
                    val entity = PlaybackResourceEntity(id, kind.name, safeTitle, extension, temp.length(), family)
                    val destination = file(entity.model())
                    // Finish the file/metadata pair even if the picker owner leaves during the final commit.
                    withContext(NonCancellable) {
                        check(temp.renameTo(destination)) { "Cannot save the imported file." }
                        try { dao.upsert(entity) } catch (failure: Throwable) {
                            destination.delete()
                            throw failure
                        }
                    }
                    entity.model()
                } finally { temp.delete() }
            }
        }

    suspend fun find(id: PlaybackResourceId): PlaybackResource? = dao.find(id.value)?.let { runCatching { it.model() }.getOrNull() }

    /** Repairs files left by a process death between rename, metadata commit and deletion. */
    suspend fun reconcile(preferences: PlayerPreferencesRepository) = withContext(Dispatchers.IO) {
        operations.withLock {
            reconcileFiles()
            updateValidatedPreferences(preferences) { it }
        }
    }

    /** Selection and deletion share the resource gate; an old UI row cannot restore a deleted ID. */
    suspend fun updatePreferences(preferences: PlayerPreferencesRepository, transform: (PlayerPreferences) -> PlayerPreferences) =
        withContext(Dispatchers.IO) { operations.withLock { updateValidatedPreferences(preferences, transform) } }

    private suspend fun updateValidatedPreferences(preferences: PlayerPreferencesRepository, transform: (PlayerPreferences) -> PlayerPreferences) {
        val available = dao.all().mapNotNull { runCatching { it.model() }.getOrNull() }
            .filter { file(it).isFile }.associateBy { it.id }
        preferences.update { stored ->
            val current = transform(stored)
            val font = current.options.subtitleFont?.let(available::get)?.takeIf { it.kind == PlaybackResourceKind.FONT }
            current.copy(options = current.options.copy(subtitleFont = font?.id, subtitleFontFamily = font?.fontFamily,
                shaders = ShaderChain(current.options.shaders.ids.filter { available[it]?.kind == PlaybackResourceKind.SHADER })))
        }
    }

    private suspend fun reconcileFiles() {
        val records = dao.all()
        val valid = records.mapNotNull { row -> runCatching {
            row.model().also { resource ->
                file(resource)
                if (resource.kind == PlaybackResourceKind.FONT) require(!resource.fontFamily.isNullOrBlank())
            }
        }.getOrNull()?.let { row.id to it } }.toMap()
        val livePaths = valid.values.map { file(it).absolutePath }.toSet()
        PlaybackResourceKind.entries.forEach { kind ->
            directory(kind).listFiles()?.forEach { entry ->
                if (entry.name.startsWith(".import-") ||
                    (entry.name.matches(Regex("[a-f0-9]{64}\\.[a-z0-9]+")) && entry.canonicalPath !in livePaths)) {
                    check(entry.delete()) { "无法清理上次中断的导入文件，请重试" }
                }
            }
        }
        database.withTransaction {
            records.filter { it.id !in valid || !file(valid.getValue(it.id)).isFile }.forEach { row ->
                database.playbackPreferencesDao().forgetResource(row.id)
                dao.delete(row.id)
            }
        }
    }

    suspend fun resolve(id: PlaybackResourceId, kind: PlaybackResourceKind): File = withContext(Dispatchers.IO) {
        val resource = requireNotNull(find(id)) { "The imported file is missing. Import it again." }
        require(resource.kind == kind) { "The imported file has the wrong resource type." }
        file(resource).also { require(it.isFile) { "The imported file is missing. Import it again." } }
    }

    fun contentUri(resource: PlaybackResource): Uri = Uri.Builder().scheme("content")
        .authority("${app.packageName}.resources").appendPath("managed")
        .appendPath(folderName(resource.kind)).appendPath("${resource.id.value}.${resource.extension}").build()

    /** Only this controlled directory is passed to the subtitle renderer. */
    fun fontsDirectory(): File = directory(PlaybackResourceKind.FONT)

    suspend fun delete(id: PlaybackResourceId, preferences: PlayerPreferencesRepository) = withContext(Dispatchers.IO) {
        operations.withLock {
            val resource = find(id) ?: return@withLock
            preferences.update { current ->
                val option = current.options
                current.copy(options = option.copy(
                    subtitleFont = option.subtitleFont?.takeUnless { it == id },
                    subtitleFontFamily = option.subtitleFontFamily.takeUnless { option.subtitleFont == id },
                    shaders = ShaderChain(option.shaders.ids.filterNot { it == id }),
                ))
            }
            withContext(NonCancellable) {
                // Keep the metadata row until deletion succeeds, so a storage failure remains retryable.
                check(!file(resource).exists() || file(resource).delete()) { "The imported file could not be removed from storage." }
                database.withTransaction {
                    database.playbackPreferencesDao().forgetResource(id.value)
                    dao.delete(id.value)
                }
            }
        }
    }

    private suspend fun copy(uri: Uri, destination: File, limit: Long) {
        val signal = CancellationSignal()
        val descriptor = AtomicReference<android.content.res.AssetFileDescriptor?>()
        providerCopies.run(onCancel = {
            signal.cancel()
            runCatching { descriptor.get()?.close() }
        }) { isActive ->
            val opened = requireNotNull(app.contentResolver.openAssetFileDescriptor(uri, "r", signal)) { "The selected file cannot be opened." }
            descriptor.set(opened)
            opened.use { asset ->
                require(asset.declaredLength <= limit) { "The selected file is too large." }
                asset.createInputStream().use { input ->
                    destination.outputStream().use { output ->
                        val buffer = ByteArray(32 * 1024)
                        var total = 0L
                        while (true) {
                            if (!isActive()) throw kotlinx.coroutines.CancellationException("Import cancelled")
                            val size = input.read(buffer)
                            if (size < 0) break
                            total += size
                            require(total <= limit) { "The selected file is too large." }
                            output.write(buffer, 0, size)
                        }
                        require(total > 0) { "The selected file is empty." }
                        output.fd.sync()
                    }
                }
            }
        }
    }

    private fun file(resource: PlaybackResource): File {
        require(resource.extension in extensions(resource.kind))
        val folder = directory(resource.kind).canonicalFile
        return File(folder, "${resource.id.value}.${resource.extension}").canonicalFile.also {
            check(it.parentFile == folder)
        }
    }
    private fun directory(kind: PlaybackResourceKind) = File(root, folderName(kind))
    private fun folderName(kind: PlaybackResourceKind) = kind.name.lowercase()

    private fun validateShader(bytes: ByteArray) {
        val text = Charsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString()
        require(!text.contains('\u0000') && text.lineSequence().any { it.startsWith("//!HOOK ") }) {
            "Choose an mpv user shader containing a HOOK directive."
        }
    }

    private fun extensions(kind: PlaybackResourceKind) = when (kind) {
        PlaybackResourceKind.FONT -> setOf("ttf", "otf", "ttc")
        PlaybackResourceKind.SHADER -> setOf("glsl", "hook")
        PlaybackResourceKind.SUBTITLE -> setOf("srt", "ass", "ssa", "vtt", "sub")
    }
    private fun limitBytes(kind: PlaybackResourceKind): Long = when (kind) {
        PlaybackResourceKind.FONT -> 20L * 1024 * 1024
        PlaybackResourceKind.SHADER -> 2L * 1024 * 1024
        PlaybackResourceKind.SUBTITLE -> 8L * 1024 * 1024
    }
    private companion object {
        const val MAX_RESOURCES = 128
        const val MAX_TOTAL_BYTES = 128L * 1024 * 1024
    }
}
