package com.siming.mobile.data

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.Charset
import java.nio.charset.CodingErrorAction
import kotlin.math.max

const val MAX_NOVEL_IMPORT_BYTES: Int = 20 * 1024 * 1024
const val MAX_NOVEL_IMPORT_CHAPTERS: Int = 2_000

data class MobileNovelImportFile(
    val filename: String,
    val bytes: ByteArray,
)

data class MobileNovelImportResult(
    val projectId: String,
    val chapterCount: Int,
    val encoding: String,
    val remote: Boolean,
    val refreshWarning: String? = null,
)

internal data class DecodedNovelText(
    val text: String,
    val encoding: String,
)

internal data class NovelChapterDraft(
    val title: String,
    val content: String,
    val wordCount: Int,
)

internal object TxtImportDecoder {
    private val gb18030: Charset = Charset.forName("GB18030")
    private val big5: Charset = Charset.forName("Big5")

    fun decode(raw: ByteArray): DecodedNovelText {
        require(raw.isNotEmpty()) { "TXT 文件内容为空" }

        bomDecode(raw)?.let { return it }

        val utf8 = decodeStrict(raw, Charsets.UTF_8)
        if (utf8 != null && quality(utf8) >= 0.90) {
            return DecodedNovelText(utf8.removePrefix("\uFEFF"), "UTF-8")
        }

        val candidates = mutableListOf<Candidate>()
        if (utf8 != null) candidates += Candidate(utf8, "UTF-8", quality(utf8))

        if (raw.size % 2 == 0 && looksLikeUtf16(raw)) {
            decodeStrict(raw, Charsets.UTF_16LE)?.let {
                candidates += Candidate(it, "UTF-16LE", quality(it))
            }
            decodeStrict(raw, Charsets.UTF_16BE)?.let {
                candidates += Candidate(it, "UTF-16BE", quality(it))
            }
        }

        decodeStrict(raw, gb18030)?.let {
            candidates += Candidate(it, "GB18030", quality(it) + simplifiedChineseBonus(it))
        }
        decodeStrict(raw, big5)?.let {
            candidates += Candidate(it, "Big5", quality(it) + traditionalChineseBonus(it))
        }

        val best = candidates.maxByOrNull { it.score }
            ?: error("无法识别 TXT 编码，请先另存为 UTF-8 或 GB18030")
        require(best.score >= 0.72) {
            "无法可靠识别 TXT 编码，请先另存为 UTF-8、GB18030 或 UTF-16"
        }
        return DecodedNovelText(best.text.removePrefix("\uFEFF"), best.encoding)
    }

    private fun bomDecode(raw: ByteArray): DecodedNovelText? {
        fun unsigned(index: Int) = raw[index].toInt() and 0xff
        return when {
            raw.size >= 3 && unsigned(0) == 0xef && unsigned(1) == 0xbb && unsigned(2) == 0xbf -> {
                val text = decodeStrict(raw.copyOfRange(3, raw.size), Charsets.UTF_8)
                    ?: error("UTF-8 BOM 文件内容损坏")
                DecodedNovelText(text, "UTF-8 BOM")
            }
            raw.size >= 2 && unsigned(0) == 0xff && unsigned(1) == 0xfe -> {
                val text = decodeStrict(raw.copyOfRange(2, raw.size), Charsets.UTF_16LE)
                    ?: error("UTF-16LE 文件内容损坏")
                DecodedNovelText(text, "UTF-16LE")
            }
            raw.size >= 2 && unsigned(0) == 0xfe && unsigned(1) == 0xff -> {
                val text = decodeStrict(raw.copyOfRange(2, raw.size), Charsets.UTF_16BE)
                    ?: error("UTF-16BE 文件内容损坏")
                DecodedNovelText(text, "UTF-16BE")
            }
            else -> null
        }
    }

    private fun decodeStrict(raw: ByteArray, charset: Charset): String? = try {
        charset.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(raw))
            .toString()
    } catch (_: CharacterCodingException) {
        null
    }

    private fun looksLikeUtf16(raw: ByteArray): Boolean {
        val sampleSize = minOf(raw.size - raw.size % 2, 8_192)
        if (sampleSize < 4) return false
        var evenZeros = 0
        var oddZeros = 0
        var index = 0
        while (index < sampleSize) {
            if (raw[index].toInt() == 0) evenZeros += 1
            if (raw[index + 1].toInt() == 0) oddZeros += 1
            index += 2
        }
        val pairs = sampleSize / 2
        val threshold = max(3, pairs / 12)
        return evenZeros >= threshold || oddZeros >= threshold
    }

    private fun quality(text: String): Double {
        if (text.isEmpty()) return -10.0
        val sample = text.take(20_000)
        var printable = 0
        var cjk = 0
        var bad = 0
        for (char in sample) {
            val code = char.code
            when {
                char == '\uFFFD' || char == '\u0000' -> bad += 8
                (code < 32 && char !in charArrayOf('\n', '\r', '\t')) ||
                    code in 0x7f..0x9f -> bad += 4
                char.isISOControl() -> bad += 2
                else -> printable += 1
            }
            if (
                code in 0x3400..0x4dbf ||
                code in 0x4e00..0x9fff ||
                code in 0xf900..0xfaff
            ) {
                cjk += 1
            }
        }
        val size = sample.length.toDouble()
        return printable / size + minOf(cjk / size, 0.25) * 0.20 - bad / size
    }

    private fun simplifiedChineseBonus(text: String): Double {
        val hints = "这为国后发里时会来个们说对从实还进"
        val sample = text.take(20_000)
        return sample.count { it in hints }.coerceAtMost(100) / 10_000.0
    }

    private fun traditionalChineseBonus(text: String): Double {
        val hints = "這為國後發裡時會來個們說對從實還進"
        val sample = text.take(20_000)
        return sample.count { it in hints }.coerceAtMost(100) / 10_000.0
    }

    private data class Candidate(
        val text: String,
        val encoding: String,
        val score: Double,
    )
}

internal object NovelImportSplitter {
    private const val FALLBACK_CHUNK_CHARS = 5_000
    private const val MAX_CHAPTER_CHARS = 200_000

    private val marker = Regex(
        """^[ \t]*((?:【[ \t]*)?(?:第[ \t]*[零〇一二三四五六七八九十百千万0-9]+[ \t]*[章节部卷]|(?:卷|部)[ \t]*[零〇一二三四五六七八九十百千万0-9]+|Chapter[ \t]+[0-9]+|Part[ \t]+[0-9]+|序章|楔子|引子|尾声)(?:[^\r\n]{0,60})?(?:[ \t]*】)?)[ \t]*$""",
        setOf(RegexOption.IGNORE_CASE, RegexOption.MULTILINE),
    )
    private val markerPrefix = Regex(
        """^(?:第[ \t]*[零〇一二三四五六七八九十百千万0-9]+[ \t]*[章节部卷]|(?:卷|部)[ \t]*[零〇一二三四五六七八九十百千万0-9]+|Chapter[ \t]+[0-9]+|Part[ \t]+[0-9]+|序章|楔子|引子|尾声)""",
        RegexOption.IGNORE_CASE,
    )
    private val titleSeparators = setOf(' ', '\t', '：', ':', '-', '—', '·', '_')
    private val sentenceEndings = setOf('。', '！', '？', '!', '?', '；', ';', '，', ',')

    private fun isLikelyChapterTitle(value: String): Boolean {
        val raw = value.trim()
        val bracketed = raw.startsWith("【") && raw.endsWith("】")
        val core = raw.removePrefix("【").removeSuffix("】").trim()
        val prefix = markerPrefix.find(core) ?: return false
        if (bracketed) return true
        val suffix = core.substring(prefix.range.last + 1)
        if (suffix.isBlank()) return true
        return suffix.first() in titleSeparators || suffix.trimEnd().last() !in sentenceEndings
    }

    fun split(content: String): List<NovelChapterDraft> {
        require(content.isNotBlank()) { "TXT 文件内容为空" }
        val matches = marker.findAll(content)
            .filter { isLikelyChapterTitle(it.value) }
            .toList()
        val chapters = if (matches.isEmpty()) {
            chunkBody("导入章节", content.trim(), FALLBACK_CHUNK_CHARS)
        } else {
            buildList {
                matches.forEachIndexed { index, match ->
                    var bodyStart = match.range.last + 1
                    while (bodyStart < content.length && content[bodyStart] in charArrayOf('\r', '\n')) {
                        bodyStart += 1
                    }
                    val bodyEnd = matches.getOrNull(index + 1)?.range?.first ?: content.length
                    val body = content.substring(bodyStart, bodyEnd).trim()
                    if (body.isNotBlank()) {
                        addAll(chunkBody(match.value.trim(), body, MAX_CHAPTER_CHARS))
                    }
                }
            }
        }
        require(chapters.isNotEmpty()) { "没有识别到可导入的章节正文" }
        require(chapters.size <= MAX_NOVEL_IMPORT_CHAPTERS) {
            "识别到 ${chapters.size} 章，超过 $MAX_NOVEL_IMPORT_CHAPTERS 章安全上限；请检查章节标题格式后重试"
        }
        return chapters
    }

    private fun chunkBody(
        baseTitle: String,
        body: String,
        chunkChars: Int,
    ): List<NovelChapterDraft> = buildList {
        var start = 0
        var partIndex = 0
        while (start < body.length) {
            val end = minOf(body.length, start + chunkChars)
            val part = body.substring(start, end).trim()
            if (part.isNotBlank()) {
                val title = if (partIndex == 0) baseTitle else "$baseTitle（续 ${partIndex + 1}）"
                add(
                    NovelChapterDraft(
                        title = title,
                        content = part,
                        wordCount = part.count { !it.isWhitespace() },
                    ),
                )
                partIndex += 1
            }
            start = end
        }
    }
}
