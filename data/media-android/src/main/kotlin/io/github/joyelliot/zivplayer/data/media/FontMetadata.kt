// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.data.media

/** Bounded SFNT name-table reader. Does not execute font shaping or trust table offsets. */
internal object FontMetadata {
    fun family(bytes: ByteArray): String {
        fun u16(offset: Int): Int {
            require(offset >= 0 && offset.toLong() + 2 <= bytes.size)
            return ((bytes[offset].toInt() and 255) shl 8) or (bytes[offset + 1].toInt() and 255)
        }
        fun u32(offset: Int): Int {
            val value = (u16(offset).toLong() shl 16) or u16(offset + 2).toLong()
            require(value <= Int.MAX_VALUE)
            return value.toInt()
        }
        fun tag(offset: Int): String {
            require(offset >= 0 && offset.toLong() + 4 <= bytes.size)
            return String(bytes, offset, 4, Charsets.ISO_8859_1)
        }
        val base = if (tag(0) == "ttcf") {
            require(u32(8) in 1..256) { "Invalid font collection." }
            u32(12)
        } else 0
        require(tag(base) in listOf("\u0000\u0001\u0000\u0000", "OTTO", "true")) { "Choose a TTF, OTF or TTC font." }
        val count = u16(base + 4)
        require(count in 1..256 && base.toLong() + 12 + count * 16L <= bytes.size)
        var nameOffset: Int? = null
        var nameLength = 0
        repeat(count) { index ->
            val entry = base + 12 + index * 16
            if (tag(entry) == "name") {
                nameOffset = u32(entry + 8)
                nameLength = u32(entry + 12)
            }
        }
        val table = requireNotNull(nameOffset) { "The font has no family name." }
        require(nameLength >= 6 && table.toLong() + nameLength <= bytes.size)
        val recordCount = u16(table + 2)
        val strings = u16(table + 4)
        require(recordCount <= 4096 && 6L + recordCount * 12L <= nameLength)
        require(strings >= 6 + recordCount * 12 && strings <= nameLength)
        val candidates = mutableListOf<Pair<Int, String>>()
        repeat(recordCount) { index ->
            val record = table + 6 + index * 12
            val platform = u16(record)
            val encoding = u16(record + 2)
            val language = u16(record + 4)
            val nameId = u16(record + 6)
            if (nameId != 1 && nameId != 16) return@repeat
            val length = u16(record + 8)
            val offset = u16(record + 10)
            if (length !in 1..640 || strings.toLong() + offset + length > nameLength) return@repeat
            val charset = when {
                platform == 0 || (platform == 3 && encoding in listOf(0, 1, 10)) -> Charsets.UTF_16BE
                platform == 1 && encoding == 0 -> Charsets.ISO_8859_1
                else -> return@repeat
            }
            if (charset == Charsets.UTF_16BE && length % 2 != 0) return@repeat
            val name = String(bytes, table + strings + offset, length, charset).trim()
            if (name.isBlank() || name.length > 160 || name.any { it.isISOControl() || it == '\uFFFD' }) return@repeat
            val score = (if (nameId == 16) 100 else 0) + (if (platform == 3) 20 else 0) +
                (if (language == 0x409 || language == 0) 10 else 0)
            candidates += score to name
        }
        return requireNotNull(candidates.maxByOrNull { it.first }?.second) { "The font has no usable family name." }
    }
}
