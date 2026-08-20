from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


# Keep the backend and Android chapter recognizers aligned. A body paragraph
# such as “第一章正文。” must not become a new chapter, while compact titles
# such as “第一章重生” and separated titles such as “第一章 风起！” remain valid.
service_path = "backend/app/services/import_service.py"
service = read(service_path)
start = service.index("CHAPTER_TITLE_RE = re.compile(")
end = service.index("\n\n\ndef _text_quality", start)
chapter_block = r'''CHAPTER_TITLE_RE = re.compile(
    r"(?im)^[ \t]*("
    r"(?:【[ \t]*)?"
    r"(?:"
    r"第[ \t]*[零〇一二三四五六七八九十百千万\d]+[ \t]*[章节部卷]"
    r"|(?:卷|部)[ \t]*[零〇一二三四五六七八九十百千万\d]+"
    r"|Chapter[ \t]+\d+"
    r"|Part[ \t]+\d+"
    r"|序章|楔子|引子|尾声"
    r")"
    r"(?:[^\r\n]{0,60})?"
    r"(?:[ \t]*】)?"
    r")[ \t]*$"
)
CHAPTER_PREFIX_RE = re.compile(
    r"(?i)^(?:"
    r"第[ \t]*[零〇一二三四五六七八九十百千万\d]+[ \t]*[章节部卷]"
    r"|(?:卷|部)[ \t]*[零〇一二三四五六七八九十百千万\d]+"
    r"|Chapter[ \t]+\d+"
    r"|Part[ \t]+\d+"
    r"|序章|楔子|引子|尾声"
    r")"
)
_CHAPTER_TITLE_SEPARATORS = set(" \t：:-—·_")
_CHAPTER_SENTENCE_ENDINGS = set("。！？!?；;，,")


def _is_likely_chapter_title(value: str) -> bool:
    raw = str(value or "").strip()
    bracketed = raw.startswith("【") and raw.endswith("】")
    core = raw.removeprefix("【").removesuffix("】").strip()
    prefix = CHAPTER_PREFIX_RE.match(core)
    if prefix is None:
        return False
    if bracketed:
        return True
    suffix = core[prefix.end() :]
    if not suffix.strip():
        return True
    return (
        suffix[0] in _CHAPTER_TITLE_SEPARATORS
        or suffix.rstrip()[-1] not in _CHAPTER_SENTENCE_ENDINGS
    )
'''
service = service[:start] + chapter_block + service[end:]
old_matches = "    matches = list(CHAPTER_TITLE_RE.finditer(text))\n"
new_matches = """    matches = [
        match
        for match in CHAPTER_TITLE_RE.finditer(text)
        if _is_likely_chapter_title(match.group(1))
    ]
"""
if service.count(old_matches) != 1:
    raise RuntimeError("backend chapter match collection changed")
service = service.replace(old_matches, new_matches, 1)
service = service.replace(
    'raise ValidationError("文件太大，最大支持 10MB")',
    'raise ValidationError("文件太大，最大支持 20 MiB")',
    1,
)
write(service_path, service)

android_path = "mobile/android/app/src/main/java/com/siming/mobile/data/NovelImport.kt"
android = read(android_path)
start = android.index("    private val marker = Regex(")
end = android.index("        val chapters = if (matches.isEmpty())", start)
android_block = r'''    private val marker = Regex(
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
'''
android = android[:start] + android_block + android[end:]
write(android_path, android)

backend_test_path = "backend/tests/test_importer.py"
backend_tests = read(backend_test_path)
anchor = "    def test_import_preview_uses_regex_chapter_boundaries_without_llm(self):\n"
new_test = '''    def test_import_preview_ignores_sentence_like_chapter_prefixes_in_body(self):
        project_id = self.create_project("Body Prefix Project")
        text = (
            "第一章 风起！\n"
            "第一章正文。这里仍然属于正文，不是新章节。\n\n"
            "第二章 云涌\n"
            "第二章正文继续。"
        )

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/import/preview",
            json={"text": text},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(
            [item["title"] for item in data["splits"]],
            ["第一章 风起！", "第二章 云涌"],
        )

'''
if backend_tests.count(anchor) != 1:
    raise RuntimeError("backend importer test anchor changed")
backend_tests = backend_tests.replace(anchor, new_test + anchor, 1)
write(backend_test_path, backend_tests)

android_test_path = "mobile/android/app/src/test/java/com/siming/mobile/data/NovelImportTest.kt"
android_tests = read(android_test_path)
anchor = "    @Test\n    fun fallbackSplitDoesNotCreateEmptyChapters() {\n"
new_test = '''    @Test
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

'''
if android_tests.count(anchor) != 1:
    raise RuntimeError("Android import test anchor changed")
android_tests = android_tests.replace(anchor, new_test + anchor, 1)
write(android_test_path, android_tests)

print("Final Android import hardening applied.")
