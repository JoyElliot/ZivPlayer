// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.content.UriPermission
import android.net.Uri
import android.provider.OpenableColumns
import io.github.joyelliot.zivplayer.core.media.DurableMediaUri
import io.github.joyelliot.zivplayer.core.media.EpochMilliseconds
import io.github.joyelliot.zivplayer.core.media.MediaRegistration
import io.github.joyelliot.zivplayer.core.media.RecentMediaRepository
import io.github.joyelliot.zivplayer.core.model.MediaId
import io.github.joyelliot.zivplayer.core.model.MediaMetadata
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.util.UUID

/** Whether the selected document can be reopened from local history after process restart. */
enum class MediaDocumentPersistence {
    PERSISTED,
    SESSION_ONLY_PERMISSION_UNAVAILABLE,
    SESSION_ONLY_HISTORY_WRITE_FAILED,
}

enum class MediaDocumentForgetResult {
    NOT_FOUND,
    REMOVED,
    REMOVED_WITH_ORPHANED_GRANT,
}

data class OpenedMediaDocument(
    val mediaId: MediaId,
    val sourceUri: String,
    val mimeType: String? = null,
    val metadata: MediaMetadata = MediaMetadata(),
    val persistence: MediaDocumentPersistence,
) {
    val isRestartSafe: Boolean
        get() = persistence == MediaDocumentPersistence.PERSISTED
}

/**
 * Turns an `OpenDocument` result into either a durable media record or an explicit session-only
 * playback request. Persistable permission is verified against [ContentResolver] rather than
 * inferred from a successful Binder call.
 */
class MediaDocumentRegistrar private constructor(
    private val coordinator: MediaDocumentRegistrationCoordinator,
    private val ioDispatcher: CoroutineDispatcher,
    private val operationMutex: Mutex,
) {

    suspend fun open(
        uri: Uri,
        persistableReadOffered: Boolean,
        visibleInHistory: Boolean = true,
    ): OpenedMediaDocument = withContext(ioDispatcher) {
        operationMutex.withLock {
            withContext(NonCancellable) {
                coordinator.open(uri.toString(), persistableReadOffered, visibleInHistory)
            }
        }
    }

    /** Removes local history and reports whether its persisted provider grant was also released. */
    suspend fun forget(mediaId: MediaId): MediaDocumentForgetResult = withContext(ioDispatcher) {
        operationMutex.withLock {
            withContext(NonCancellable) {
                coordinator.forget(mediaId)
            }
        }
    }

    companion object {
        fun create(
            context: Context,
            repository: RecentMediaRepository,
            operationMutex: Mutex = Mutex(),
        ): MediaDocumentRegistrar = MediaDocumentRegistrar(
            coordinator = MediaDocumentRegistrationCoordinator(
                repository = repository,
                documentAccess = AndroidDocumentAccess(context.applicationContext.contentResolver),
                clock = System::currentTimeMillis,
                sessionIdFactory = { MediaId("session:${UUID.randomUUID()}") },
            ),
            ioDispatcher = Dispatchers.IO,
            operationMutex = operationMutex,
        )
    }
}

internal class MediaDocumentRegistrationCoordinator(
    private val repository: RecentMediaRepository,
    private val documentAccess: DocumentAccess,
    private val clock: () -> Long,
    private val sessionIdFactory: () -> MediaId,
) {
    suspend fun open(
        sourceUri: String,
        persistableReadOffered: Boolean = true,
        visibleInHistory: Boolean = true,
    ): OpenedMediaDocument {
        require(sourceUri.isNotBlank()) { "Selected document URI must not be blank." }
        val grant = try {
            documentAccess.acquirePersistedReadGrant(
                sourceUri = sourceUri,
                mayTakeNewGrant = persistableReadOffered,
            )
        } catch (failure: CancellationException) {
            throw failure
        } catch (_: Exception) {
            PersistedReadGrant.UNAVAILABLE
        }
        val details = try {
            documentAccess.readDetails(sourceUri)
        } catch (failure: CancellationException) {
            throw failure
        } catch (_: Exception) {
            MediaDocumentDetails()
        }
        if (grant == PersistedReadGrant.UNAVAILABLE) {
            return sessionOnly(
                sourceUri = sourceUri,
                details = details,
                persistence = MediaDocumentPersistence.SESSION_ONLY_PERMISSION_UNAVAILABLE,
            )
        }

        try {
            val record = repository.remember(
                MediaRegistration(
                    sourceUri = DurableMediaUri(sourceUri),
                    mimeType = details.mimeType,
                    metadata = details.metadata,
                    openedAt = EpochMilliseconds(clock()),
                    visibleInHistory = visibleInHistory,
                ),
            )
            return OpenedMediaDocument(
                mediaId = record.id,
                sourceUri = record.sourceUri.value,
                mimeType = record.mimeType,
                metadata = record.metadata,
                persistence = MediaDocumentPersistence.PERSISTED,
            )
        } catch (failure: CancellationException) {
            if (grant == PersistedReadGrant.ACQUIRED) {
                documentAccess.releasePersistedReadGrant(sourceUri)
            }
            throw failure
        } catch (_: Exception) {
            if (grant == PersistedReadGrant.ACQUIRED) {
                documentAccess.releasePersistedReadGrant(sourceUri)
            }
            return sessionOnly(
                sourceUri = sourceUri,
                details = details,
                persistence = MediaDocumentPersistence.SESSION_ONLY_HISTORY_WRITE_FAILED,
            )
        }
    }

    suspend fun forget(mediaId: MediaId): MediaDocumentForgetResult {
        val stored = repository.findById(mediaId) ?: return MediaDocumentForgetResult.NOT_FOUND
        if (!repository.remove(mediaId)) {
            return MediaDocumentForgetResult.NOT_FOUND
        }
        return if (documentAccess.releasePersistedReadGrant(stored.record.sourceUri.value)) {
            MediaDocumentForgetResult.REMOVED
        } else {
            MediaDocumentForgetResult.REMOVED_WITH_ORPHANED_GRANT
        }
    }

    private fun sessionOnly(
        sourceUri: String,
        details: MediaDocumentDetails,
        persistence: MediaDocumentPersistence,
    ): OpenedMediaDocument = OpenedMediaDocument(
        mediaId = sessionIdFactory(),
        sourceUri = sourceUri,
        mimeType = details.mimeType,
        metadata = details.metadata,
        persistence = persistence,
    )
}

internal enum class PersistedReadGrant {
    PREEXISTING,
    ACQUIRED,
    UNAVAILABLE,
}

internal data class MediaDocumentDetails(
    val mimeType: String? = null,
    val metadata: MediaMetadata = MediaMetadata(),
)

internal interface DocumentAccess {
    fun acquirePersistedReadGrant(
        sourceUri: String,
        mayTakeNewGrant: Boolean,
    ): PersistedReadGrant

    fun releasePersistedReadGrant(sourceUri: String): Boolean

    fun readDetails(sourceUri: String): MediaDocumentDetails
}

private class AndroidDocumentAccess(
    private val contentResolver: ContentResolver,
) : DocumentAccess {
    override fun acquirePersistedReadGrant(
        sourceUri: String,
        mayTakeNewGrant: Boolean,
    ): PersistedReadGrant {
        val uri = Uri.parse(sourceUri)
        if (uri.scheme != ContentResolver.SCHEME_CONTENT) {
            return PersistedReadGrant.UNAVAILABLE
        }
        if (hasPersistedReadGrant(uri)) {
            return PersistedReadGrant.PREEXISTING
        }
        if (!mayTakeNewGrant) {
            return PersistedReadGrant.UNAVAILABLE
        }
        runCatching {
            contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION,
            )
        }
        return if (hasPersistedReadGrant(uri)) {
            PersistedReadGrant.ACQUIRED
        } else {
            PersistedReadGrant.UNAVAILABLE
        }
    }

    override fun releasePersistedReadGrant(sourceUri: String): Boolean {
        val uri = Uri.parse(sourceUri)
        val permissions = persistedPermissions(uri).getOrElse { return false }
        if (permissions.none(UriPermission::isReadPermission)) {
            return true
        }
        if (runCatching {
            contentResolver.releasePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION,
            )
        }.isFailure) {
            return false
        }
        return persistedPermissions(uri).getOrElse { return false }
            .none(UriPermission::isReadPermission)
    }

    override fun readDetails(sourceUri: String): MediaDocumentDetails {
        val uri = Uri.parse(sourceUri)
        val mimeType = runCatching { contentResolver.getType(uri) }
            .getOrNull()
            ?.takeUnless(String::isBlank)
        val displayName = runCatching {
            contentResolver.query(
                uri,
                arrayOf(OpenableColumns.DISPLAY_NAME),
                null,
                null,
                null,
            )?.use { cursor ->
                val displayNameColumn = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (displayNameColumn >= 0 && cursor.moveToFirst()) {
                    cursor.getString(displayNameColumn)?.takeUnless(String::isBlank)
                } else {
                    null
                }
            }
        }.getOrNull()
        return MediaDocumentDetails(
            mimeType = mimeType,
            metadata = MediaMetadata(title = displayName),
        )
    }

    private fun hasPersistedReadGrant(uri: Uri): Boolean = runCatching {
        contentResolver.hasPersistedDocumentRead(uri)
    }.getOrDefault(false)

    private fun persistedPermissions(uri: Uri): Result<List<UriPermission>> = runCatching {
        contentResolver.persistedUriPermissions.filter { permission ->
            permission.uri == uri
        }
    }
}
