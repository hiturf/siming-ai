package com.siming.mobile.data

import java.nio.charset.Charset
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class NovelImportTest {
    @Test
    fun decodesUtf8AndUtf8Bom() {
        val text = "第一章 风起\n这里是正文。"
        val plain = TxtImportDecoder.decode(text.toByteArray(Charsets.UTF_8))
        val bom = TxtImportDecoder.decode(
            byteArrayOf(0xef.toByte(), 0xbb.toByte(), 0xbf.toByte()) +
                text.toByteArray(Charsets.UTF_8),
        )

        assertEquals(text, plain.text)
        assertEquals("UTF-8", plain.encoding)
        assertEquals(text, bom.text)
        assertEquals("UTF-8 BOM", bom.encoding)
    }

    @Test
    fun decodesGb18030WithoutReplacementCharacters() {
        val text = "第一章 风起\n陆糖看见归墟阵重新亮起。"
        val decoded = TxtImportDecoder.decode(text.toByteArray(Charset.forName("GB18030")))

        assertEquals(text, decoded.text)
        assertEquals("GB18030", decoded.encoding)
        assertFalse(decoded.text.contains('\uFFFD'))
    }

    @Test
    fun decodesUtf16LittleEndianWithoutBom() {
        val text = "第一章 风起\n这是 UTF-16 正文。"
        val decoded = TxtImportDecoder.decode(text.toByteArray(Charsets.UTF_16LE))

        assertEquals(text, decoded.text)
        assertEquals("UTF-16LE", decoded.encoding)
    }

    @Test
    fun splitsStandardChineseChapterTitles() {
        val text = """
            第一章 风起
            第一章正文。

            第二章 云涌
            第二章正文。
        """.trimIndent()

        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertEquals(listOf("第一章 风起", "第二章 云涌"), chapters.map { it.title })
        assertTrue(chapters[0].content.startsWith("第一章正文"))
        assertTrue(chapters[1].content.startsWith("第二章正文"))
    }

    @Test
    fun ignoresSentenceLikeChapterPrefixesInsideBody() {
        val text = """
            第一章 风起！
            第一章正文。这里仍然属于正文，不是新章节。

            第二章 云涌
            第二章正文继续。
        """.trimIndent()

        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertEquals(listOf("第一章 风起！", "第二章 云涌"), chapters.map { it.title })
        assertTrue(chapters.first().content.contains("第一章正文"))
    }

    @Test
    fun fallbackSplitDoesNotCreateEmptyChapters() {
        val text = "正文".repeat(3_000)
        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertTrue(chapters.all { it.content.isNotBlank() })
    }
}
