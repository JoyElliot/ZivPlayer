// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import android.content.ContentResolver
import android.net.Uri
import android.provider.DocumentsContract

internal fun grantCoversDocument(grant: Uri, document: Uri): Boolean {
    if (grant == document) return true
    if (grant.authority != document.authority || grant.scheme != ContentResolver.SCHEME_CONTENT ||
        document.scheme != ContentResolver.SCHEME_CONTENT) return false
    return runCatching {
        DocumentsContract.isTreeUri(grant) && DocumentsContract.isTreeUri(document) &&
            DocumentsContract.getTreeDocumentId(grant) == DocumentsContract.getTreeDocumentId(document)
    }.getOrDefault(false)
}

internal fun ContentResolver.hasPersistedDocumentRead(uri: Uri): Boolean =
    persistedUriPermissions.any { it.isReadPermission && grantCoversDocument(it.uri, uri) }
