"""Read-only access to files explicitly imported into the Siming content root."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session


async def list_imported_files(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    from app.services.content_store import content_root

    imported_dir = content_root() / ".imported"
    files: list[dict[str, Any]] = []
    if imported_dir.exists():
        for path in sorted(imported_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            stat = path.stat()
            files.append({
                "filename": path.name,
                "path": str(path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {
        "tool": "list_imported_files",
        "status": "ok",
        "data": {"files": files, "directory": str(imported_dir)},
    }


async def read_imported_file(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    from app.services.content_store import content_root

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return {"tool": "read_imported_file", "status": "skipped", "detail": "filename is required", "data": None}
    imported_dir = (content_root() / ".imported").resolve()
    file_path = (imported_dir / filename).resolve()
    if not file_path.is_relative_to(imported_dir):
        return {"tool": "read_imported_file", "status": "skipped", "detail": "访问被拒绝", "data": None}
    if not file_path.is_file():
        return {"tool": "read_imported_file", "status": "skipped", "detail": "文件不存在", "data": None}
    try:
        max_size = max(1, int(args.get("max_size") or 50_000))
    except (TypeError, ValueError):
        max_size = 50_000
    full_content = file_path.read_text(encoding="utf-8")
    content = full_content[:max_size]
    if len(full_content) > max_size:
        content += f"\n...(文件已截断，共{len(full_content)}字)"
    return {
        "tool": "read_imported_file",
        "status": "ok",
        "data": {
            "filename": filename,
            "content": content,
            "size": len(full_content),
            "path": str(file_path),
        },
    }
