// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.*
import org.junit.Test

class FontMetadataTest {
    @Test fun readsUnicodeFamilyAndRejectsEscapingTableOffsets() {
        val bytes = font("字体 Family")
        assertEquals("字体 Family", FontMetadata.family(bytes))
        val escaped = bytes.copyOf()
        ByteBuffer.wrap(escaped).putInt(20, Int.MAX_VALUE)
        assertThrows(IllegalArgumentException::class.java) { FontMetadata.family(escaped) }
        assertThrows(IllegalArgumentException::class.java) { FontMetadata.family(bytes.copyOf(30)) }
    }

    @Test fun readsCollectionFirstFaceAndRejectsInvalidSignatureOrControlNames() {
        val regular = font("Collection")
        val ttc = ByteBuffer.allocate(regular.size + 16).order(ByteOrder.BIG_ENDIAN)
        ttc.put("ttcf".toByteArray()).putInt(0x00010000).putInt(1).putInt(16).put(regular)
        ttc.putInt(16 + 20, 16 + 28)
        assertEquals("Collection", FontMetadata.family(ttc.array()))
        assertThrows(IllegalArgumentException::class.java) { FontMetadata.family("not a font".toByteArray()) }
        assertThrows(IllegalArgumentException::class.java) { FontMetadata.family(font("Unsafe\u0000Name")) }
    }

    private fun font(name: String): ByteArray {
        val encoded = name.toByteArray(Charsets.UTF_16BE)
        return ByteBuffer.allocate(46 + encoded.size).order(ByteOrder.BIG_ENDIAN).apply {
            putInt(0x00010000); putShort(1); putShort(0); putShort(0); putShort(0)
            put("name".toByteArray()); putInt(0); putInt(28); putInt(18 + encoded.size)
            putShort(0); putShort(1); putShort(18)
            putShort(3); putShort(1); putShort(0x409); putShort(1)
            putShort(encoded.size.toShort()); putShort(0); put(encoded)
        }.array()
    }
}
