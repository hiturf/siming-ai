"""Read, restore, diff, and delete chapter workspace tools.

Creating and editing chapter prose is intentionally absent here. AI writing
produces a pending ``ChapterDraft``; only the author-facing chapter HTTP API
can turn the current editor text into an official chapter.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    ChapterSummary,
    Character,
    CharacterChangeLog,
    CharacterTimeline,
    Project,
)
from ....modules.story.application.content_sync import queue_content_sync
from ....modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ....services.chapter_service import (
    diff_snapshots,
    restore_chapter_from_snapshot,
    snapshot_to_item,
)
from ....services.narrative_governance import mark_governance_items_stale_for_chapter
from ....services.narrative_ledger import restore_ledger_checkpoint
from ..utils import find_outline_by_title_or_id


def _find_chapter(db: Session, project_id: str, args: dict[str, Any]) -> Chapter | None:
    for ref in (args.get("id"), args.get("chapter_id")):
        text = str(ref or "").strip()
        if text:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id,
                Chapter.id == text,
            ).first()
            if chapter:
                return chapter
    title_ref = str(args.get("title") or args.get("chapter_title") or "").strip()
    if title_ref:
        chapter = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.title == title_ref)
            .order_by(Chapter.created_at.desc())
            .first()
        )
        if chapter:
            return chapter
    outline_node = None
    for ref in (
        args.get("outline_node_id"),
        args.get("outline_node_title"),
        args.get("outline_title"),
    ):
        outline_node = find_outline_by_title_or_id(
            db, project_id, ref, node_type="chapter"
        )
        if outline_node:
            break
    if outline_node:
        return (
            db.query(Chapter)
            .filter(
                Chapter.project_id == project_id,
                Chapter.outline_node_id == outline_node.id,
            )
            .order_by(Chapter.created_at.desc())
            .first()
        )
    return None


def _chapter_version_data(chapter: Chapter) -> dict[str, Any]:
    return {
        "id": chapter.id,
        "chapter_id": chapter.id,
        "title": chapter.title,
        "word_count": chapter.word_count or 0,
        "current_version": chapter.current_version or 1,
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }


def _chapter_snapshots(db: Session, chapter: Chapter) -> list[ChapterSnapshot]:
    return (
        db.query(ChapterSnapshot)
        .filter(ChapterSnapshot.chapter_id == chapter.id)
        .order_by(
            ChapterSnapshot.version_number.desc(),
            ChapterSnapshot.created_at.desc(),
        )
        .all()
    )


def _find_snapshot(
    db: Session,
    chapter: Chapter,
    args: dict[str, Any],
) -> ChapterSnapshot | None:
    snapshot_id = str(args.get("snapshot_id") or args.get("version_id") or "").strip()
    if snapshot_id:
        return (
            db.query(ChapterSnapshot)
            .filter(
                ChapterSnapshot.chapter_id == chapter.id,
                ChapterSnapshot.id == snapshot_id,
            )
            .first()
        )
    raw_version = args.get("version_number")
    if raw_version in (None, ""):
        raw_version = args.get("version")
    if raw_version not in (None, ""):
        try:
            version_number = int(raw_version)
        except (TypeError, ValueError):
            version_number = None
        if version_number:
            return (
                db.query(ChapterSnapshot)
                .filter(
                    ChapterSnapshot.chapter_id == chapter.id,
                    ChapterSnapshot.version_number == version_number,
                )
                .order_by(ChapterSnapshot.created_at.desc())
                .first()
            )
    snapshots = _chapter_snapshots(db, chapter)
    target = str(args.get("target") or "previous").strip().lower()
    if target in {"first", "initial", "oldest", "最初", "初版", "第一版"}:
        return snapshots[-1] if snapshots else None
    if target in {"latest", "newest", "最新"}:
        return snapshots[0] if snapshots else None
    current_version = chapter.current_version or 1
    for snapshot in snapshots:
        if (snapshot.version_number or 0) < current_version:
            return snapshot
    return None


async def list_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {
            "tool": "list_chapter_versions",
            "status": "skipped",
            "detail": "未找到章节",
            "data": None,
        }
    snapshots = _chapter_snapshots(db, chapter)
    items = [snapshot_to_item(snapshot) for snapshot in snapshots]
    return {
        "tool": "list_chapter_versions",
        "status": "ok",
        "detail": f"章节「{chapter.title}」共有 {len(items)} 个版本快照，当前 v{chapter.current_version or 1}",
        "data": {
            "chapter": _chapter_version_data(chapter),
            "items": items,
            "total": len(items),
        },
    }


async def restore_chapter_version(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "未找到章节",
            "data": None,
        }
    snapshot = _find_snapshot(db, chapter, args)
    if not snapshot:
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "没有找到可恢复的版本；请先调用 list_chapter_versions 查看可用快照",
            "data": {
                "chapter": _chapter_version_data(chapter),
                "items": [snapshot_to_item(item) for item in _chapter_snapshots(db, chapter)],
            },
        }
    if (snapshot.version_number or 0) >= (chapter.current_version or 1) and not (
        args.get("snapshot_id")
        or args.get("version_id")
        or args.get("version_number")
    ):
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "当前章节没有更早的可回退版本",
            "data": {
                "chapter": _chapter_version_data(chapter),
                "items": [snapshot_to_item(item) for item in _chapter_snapshots(db, chapter)],
            },
        }
    restored = restore_chapter_from_snapshot(db, chapter, snapshot)
    chapter.cataloging_required = True
    ledger_restore = restore_ledger_checkpoint(db, project_id, chapter, snapshot.id)
    stale_count = mark_governance_items_stale_for_chapter(
        db,
        project_id,
        chapter.id,
        reason=f"{chapter.title} 已恢复历史版本，原治理结论需要复检",
        actor="chapter_restore",
    )
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHAPTER,
                entity_id=chapter.id,
                source="workspace_tool",
            ),
        )
    result = {
        "tool": "restore_chapter_version",
        "status": "ok",
        "detail": f"已将「{chapter.title}」恢复到 v{snapshot.version_number}，当前记录为 v{chapter.current_version or 1}",
        "data": {
            "chapter": _chapter_version_data(chapter),
            "restored_from": snapshot_to_item(snapshot),
            "restore_snapshot": snapshot_to_item(restored),
            "content_preview": (chapter.content or "")[:500],
            "ledger_checkpoint_id": ledger_restore["ledger_checkpoint_id"],
            "ledger_restored_count": ledger_restore["restored_count"],
            "ledger_conflicts": ledger_restore["conflicts"],
            "governance_invalidated_count": stale_count,
        },
    }
    commit_session(db)
    return result


async def diff_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {
            "tool": "diff_chapter_versions",
            "status": "skipped",
            "detail": "未找到章节",
            "data": None,
        }
    from_args = dict(args)
    to_args = dict(args)
    from_args["snapshot_id"] = args.get("from_snapshot_id") or args.get("base_snapshot_id")
    to_args["snapshot_id"] = args.get("to_snapshot_id") or args.get("target_snapshot_id")
    if not from_args["snapshot_id"]:
        from_args["version_number"] = args.get("from_version")
    if not to_args["snapshot_id"]:
        to_args["version_number"] = args.get("to_version")
    from_snapshot = _find_snapshot(db, chapter, from_args)
    to_snapshot = _find_snapshot(db, chapter, to_args)
    if not from_snapshot or not to_snapshot:
        return {
            "tool": "diff_chapter_versions",
            "status": "skipped",
            "detail": "需要两个可识别的版本；请先调用 list_chapter_versions",
            "data": {
                "chapter": _chapter_version_data(chapter),
                "items": [snapshot_to_item(item) for item in _chapter_snapshots(db, chapter)],
            },
        }
    return {
        "tool": "diff_chapter_versions",
        "status": "ok",
        "detail": f"已对比 v{from_snapshot.version_number} 与 v{to_snapshot.version_number}",
        "data": diff_snapshots(from_snapshot, to_snapshot),
    }


async def delete_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "delete_chapter", "status": "skipped", "detail": "未找到章节"}

    title = chapter.title
    chapter_id = chapter.id
    content_file_path = chapter.content_file_path
    change_logs = db.query(CharacterChangeLog).filter(
        CharacterChangeLog.chapter_id == chapter.id,
        CharacterChangeLog.confirmed.is_(True),
    ).all()
    reverted: list[str] = []
    for log_entry in change_logs:
        character = db.query(Character).filter(Character.id == log_entry.character_id).first()
        if character and log_entry.field_name in {
            "abilities",
            "personality",
            "background",
            "appearance",
        }:
            old_value = log_entry.old_value
            if old_value and old_value != "（档案中无记录）":
                setattr(character, log_entry.field_name, old_value)
                reverted.append(character.name)
    if reverted:
        db.flush()

    db.query(CharacterChangeLog).filter(CharacterChangeLog.chapter_id == chapter.id).delete()
    db.query(CharacterTimeline).filter(CharacterTimeline.chapter_id == chapter.id).delete()
    db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
    db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter.id).delete()
    db.delete(chapter)
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.FILE_DELETE,
                entity_id=chapter_id,
                payload={
                    "folder_path": project.folder_path,
                    "relative_path": content_file_path,
                },
                source="workspace_tool",
            ),
        )

    detail = f"已删除章节：{title}"
    if reverted:
        detail += f"，已回退 {len(reverted)} 个角色的状态（{', '.join(reverted)}）"
    return {"tool": "delete_chapter", "status": "ok", "detail": detail}


__all__ = [
    "delete_chapter",
    "diff_chapter_versions",
    "list_chapter_versions",
    "restore_chapter_version",
]
