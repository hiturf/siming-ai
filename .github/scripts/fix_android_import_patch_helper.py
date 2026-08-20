from pathlib import Path

path = Path(".github/scripts/apply_android_import_fix.py")
source = path.read_text(encoding="utf-8")
old = "updated, count = re.subn(pattern, replacement, content, count=1, flags=flags)"
new = "updated, count = re.subn(pattern, lambda _match: replacement, content, count=1, flags=flags)"
if old not in source:
    raise SystemExit("patch helper target was not found")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
