// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import android.app.Activity
import android.content.Intent
import android.net.Uri
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OpenMediaDocumentContractTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun createIntentRequestsOpenableAudioAndVideoWithDurableReadFlags() {
        val mimeTypes = arrayOf("video/*", "audio/*")

        val intent = OpenMediaDocument().createIntent(context, mimeTypes)

        assertEquals(Intent.ACTION_OPEN_DOCUMENT, intent.action)
        assertEquals("*/*", intent.type)
        assertTrue(Intent.CATEGORY_OPENABLE in checkNotNull(intent.categories))
        assertArrayEquals(mimeTypes, intent.getStringArrayExtra(Intent.EXTRA_MIME_TYPES))
        assertTrue(intent.flags hasFlag Intent.FLAG_GRANT_READ_URI_PERMISSION)
        assertTrue(intent.flags hasFlag Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        assertFalse(intent.flags hasFlag Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
    }

    @Test
    fun parseResultKeepsProviderFlagsAndRejectsIncompleteResults() {
        val uri = Uri.parse("content://documents/video/42")
        val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or
            Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        val result = OpenMediaDocument().parseResult(
            Activity.RESULT_OK,
            Intent().setData(uri).addFlags(flags),
        )

        assertEquals(OpenMediaDocumentResult(uri, flags), result)
        assertTrue(checkNotNull(result).offersPersistableRead)
        assertNull(OpenMediaDocument().parseResult(Activity.RESULT_CANCELED, Intent()))
        assertNull(OpenMediaDocument().parseResult(Activity.RESULT_OK, null))
        assertNull(OpenMediaDocument().parseResult(Activity.RESULT_OK, Intent()))
    }

    @Test
    fun durableReadOfferRequiresBothReadAndPersistableFlags() {
        val uri = Uri.parse("content://documents/audio/7")

        assertFalse(OpenMediaDocumentResult(uri, 0).offersPersistableRead)
        assertFalse(
            OpenMediaDocumentResult(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION,
            ).offersPersistableRead,
        )
        assertFalse(
            OpenMediaDocumentResult(
                uri,
                Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
            ).offersPersistableRead,
        )
        assertTrue(
            OpenMediaDocumentResult(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
            ).offersPersistableRead,
        )
    }
}

private infix fun Int.hasFlag(flag: Int): Boolean = this and flag != 0
