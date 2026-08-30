// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.result.contract.ActivityResultContract

data class OpenMediaDocumentResult(
    val uri: Uri,
    val resultFlags: Int,
) {
    val offersPersistableRead: Boolean
        get() = resultFlags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0 &&
            resultFlags and Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION != 0
}

/** Openable-document contract that retains the provider's actual returned grant flags. */
internal class OpenMediaDocument :
    ActivityResultContract<Array<String>, OpenMediaDocumentResult?>() {
    override fun createIntent(context: Context, input: Array<String>): Intent =
        Intent(Intent.ACTION_OPEN_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*")
            .putExtra(Intent.EXTRA_MIME_TYPES, input)
            .addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
            )

    override fun parseResult(resultCode: Int, intent: Intent?): OpenMediaDocumentResult? {
        if (resultCode != Activity.RESULT_OK) {
            return null
        }
        val uri = intent?.data ?: return null
        return OpenMediaDocumentResult(uri = uri, resultFlags = intent.flags)
    }
}
