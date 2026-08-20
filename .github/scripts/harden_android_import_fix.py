from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/NovelImport.kt")
source = path.read_text(encoding="utf-8")
block_start = source.index("    private val marker = Regex(")
block_end = source.index("    fun split(content: String)", block_start)
marker_block = source[block_start:block_end].replace(r"\s*", r"[ \t]*")
source = source[:block_start] + marker_block + source[block_end:]
old = "        val matches = marker.findAll(content).toList()\n"
new = """        val matches = marker.findAll(content)
            .filterNot { match ->
                match.value.trimEnd().lastOrNull()?.let { it in \"。！？!?；;，,\" } == true
            }
            .toList()
"""
if old not in source:
    raise SystemExit("chapter match collection target was not found")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
