"""Short-lived generated draft cache for workspace tools.

The assistant model should not have to copy a full chapter body back into a
tool-call argument. Tool-call arguments are a common place for long text to get
truncated, so writers store the full text here and write tools can resolve it by
draft id or by matching a provided prefix.

Drafts are persisted to SQLite (chapter_drafts table) so they survive server
restarts. The in-memory OrderedDict acts as an L1 cache for fast lookups.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.architecture.uow import commit_session

from ...core.utils import count_words

MAX_CHAPTER_DRAFTS = 64

_CHAPTER_DRAFTS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def store_chapter_draft(
    *,
    project_id: str,
    content: str,
    title: str = "",
    outline_node_id: str | None = None,
    context_manifest_id: str | None = None,
    db: Any = None,
) -> str:
    draft_id = str(uuid4())
    _CHAPTER_DRAFTS[draft_id] = {
        "project_id": project_id,
        "title": title,
        "outline_node_id": outline_node_id or "",
        "context_manifest_id": context_manifest_id or "",
        "saved_chapter_id": "",
        "status": "pending",
        "content": content,
        "created_at": datetime.utcnow(),
    }
    _CHAPTER_DRAFTS.move_to_end(draft_id)
    while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
        _CHAPTER_DRAFTS.popitem(last=False)

    if db is not None:
        from ...database.models import ChapterDraft

        pending = db.query(ChapterDraft).filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        )
        for previous in pending.all():
            previous.status = "superseded"
            cached = _CHAPTER_DRAFTS.get(str(previous.id))
            if cached:
                cached["status"] = "superseded"

        row = ChapterDraft(
            id=draft_id,
            project_id=project_id,
            title=title or "",
            outline_node_id=outline_node_id or None,
            context_manifest_id=context_manifest_id or None,
            status="pending",
            content=content,
        )
        db.add(row)
        commit_session(db)

    return draft_id


def get_chapter_draft(project_id: str, draft_id: str | None, *, db: Any = None) -> str | None:
    if not draft_id:
        return None
    entry = _CHAPTER_DRAFTS.get(str(draft_id))
    if entry and entry.get("project_id") == project_id:
        _CHAPTER_DRAFTS.move_to_end(str(draft_id))
        return str(entry.get("content") or "")

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == str(draft_id), ChapterDraft.project_id == project_id)
                .first()
            )
            if row:
                content = str(row.content or "")
                _CHAPTER_DRAFTS[str(draft_id)] = {
                    "project_id": project_id,
                    "title": row.title or "",
                    "outline_node_id": row.outline_node_id or "",
                    "context_manifest_id": row.context_manifest_id or "",
                    "saved_chapter_id": row.saved_chapter_id or "",
                    "status": row.status or "pending",
                    "content": content,
                    "created_at": row.created_at,
                }
                _CHAPTER_DRAFTS.move_to_end(str(draft_id))
                while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
                    _CHAPTER_DRAFTS.popitem(last=False)
                return content
        except Exception:
            pass

    return None


def get_chapter_draft_meta(project_id: str, draft_id: str | None, *, db: Any = None) -> dict[str, Any] | None:
    if not draft_id:
        return None
    entry = _CHAPTER_DRAFTS.get(str(draft_id))
    if entry and entry.get("project_id") == project_id:
        return {
            "title": str(entry.get("title") or ""),
            "outline_node_id": str(entry.get("outline_node_id") or ""),
            "context_manifest_id": str(entry.get("context_manifest_id") or ""),
            "saved_chapter_id": str(entry.get("saved_chapter_id") or ""),
            "status": str(entry.get("status") or "pending"),
            "content": str(entry.get("content") or ""),
        }

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == str(draft_id), ChapterDraft.project_id == project_id)
                .first()
            )
            if row:
                return {
                    "title": row.title or "",
                    "outline_node_id": row.outline_node_id or "",
                    "context_manifest_id": row.context_manifest_id or "",
                    "saved_chapter_id": row.saved_chapter_id or "",
                    "status": row.status or "pending",
                    "content": row.content or "",
                }
        except Exception:
            pass
    return None


def _looks_like_prefix(prefix: str, full: str) -> bool:
    prefix = prefix.strip()
    full = full.strip()
    if not prefix:
        return True
    if len(full) <= len(prefix):
        return False
    head = full[: max(200, min(len(prefix), 1200))]
    return head.startswith(prefix[: len(head)]) or prefix[:200] in full[:1200]


def resolve_chapter_draft_content(
    *,
    project_id: str,
    provided_content: str = "",
    draft_id: str | None = None,
    outline_node_id: str | None = None,
    db: Any = None,
) -> str:
    """Return the best full chapter content for a write/evaluation action."""
    provided = provided_content or ""
    direct = get_chapter_draft(project_id, draft_id, db=db)
    if direct and len(direct.strip()) > len(provided.strip()):
        return direct

    outline_id = str(outline_node_id or "").strip()
    for _id, entry in reversed(_CHAPTER_DRAFTS.items()):
        if entry.get("project_id") != project_id:
            continue
        if outline_id and str(entry.get("outline_node_id") or "") != outline_id:
            continue
        content = str(entry.get("content") or "")
        if content and _looks_like_prefix(provided, content):
            return content

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            query = db.query(ChapterDraft).filter(
                ChapterDraft.project_id == project_id,
                ChapterDraft.status == "pending",
            )
            if outline_id:
                query = query.filter(ChapterDraft.outline_node_id == outline_id)
            rows = query.order_by(ChapterDraft.created_at.desc()).limit(10).all()
            for row in rows:
                content = str(row.content or "")
                if content and _looks_like_prefix(provided, content):
                    _CHAPTER_DRAFTS[str(row.id)] = {
                        "project_id": project_id,
                        "title": row.title or "",
                        "outline_node_id": row.outline_node_id or "",
                        "context_manifest_id": row.context_manifest_id or "",
                        "saved_chapter_id": row.saved_chapter_id or "",
                        "status": row.status or "pending",
                        "content": content,
                        "created_at": row.created_at,
                    }
                    _CHAPTER_DRAFTS.move_to_end(str(row.id))
                    while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
                        _CHAPTER_DRAFTS.popitem(last=False)
                    return content
        except Exception:
            pass

    return provided


def find_pending_chapter_draft(
    db: Any,
    project_id: str,
) -> Any | None:
    """Return the one author-visible draft that blocks further generation."""
    from ...database.models import ChapterDraft

    return (
        db.query(ChapterDraft)
        .filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        )
        .order_by(ChapterDraft.updated_at.desc(), ChapterDraft.created_at.desc())
        .first()
    )


def pending_chapter_draft_ids(db: Any, project_id: str) -> set[str]:
    from ...database.models import ChapterDraft

    return {
        str(row[0])
        for row in db.query(ChapterDraft.id).filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        ).all()
    }


def latest_pending_chapter_draft(db: Any, project_id: str) -> Any | None:
    from ...database.models import ChapterDraft

    return (
        db.query(ChapterDraft)
        .filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        )
        .order_by(ChapterDraft.updated_at.desc(), ChapterDraft.created_at.desc())
        .first()
    )


def find_new_pending_chapter_draft(
    db: Any,
    project_id: str,
    excluded_ids: set[str],
) -> Any | None:
    from ...database.models import ChapterDraft

    query = db.query(ChapterDraft).filter(
        ChapterDraft.project_id == project_id,
        ChapterDraft.status == "pending",
    )
    if excluded_ids:
        query = query.filter(ChapterDraft.id.notin_(excluded_ids))
    return query.order_by(
        ChapterDraft.created_at.desc(),
        ChapterDraft.id.desc(),
    ).first()


def find_chapter_draft(db: Any, project_id: str, draft_id: str) -> Any | None:
    from ...database.models import ChapterDraft

    return db.query(ChapterDraft).filter(
        ChapterDraft.id == draft_id,
        ChapterDraft.project_id == project_id,
    ).first()


def ensure_generated_draft_outline_is_unused(
    db: Any,
    project_id: str,
    outline_node_id: str | None,
) -> None:
    """Reject promotion when the selected outline already owns formal prose."""
    if not outline_node_id:
        return

    from ...core.exceptions import ValidationError
    from ...database.models import Chapter

    existing = db.query(Chapter).filter(
        Chapter.project_id == project_id,
        Chapter.outline_node_id == outline_node_id,
    ).first()
    if existing:
        raise ValidationError(
            "该大纲已关联正式章节；AI 新章草稿不能覆盖或伪装成已有章节，"
            "请选择尚未写入正文的章级大纲"
        )


def pending_draft_block_result(tool: str, draft: Any) -> dict[str, Any]:
    from .turn_control import AssistantTurnDirective, apply_turn_directive

    return apply_turn_directive(
        {
            "tool": tool,
            "status": "blocked",
            "detail": "当前章节草稿尚未保存并完成建档，本轮未生成下一章。",
            "data": {
                "blocking_draft_id": draft.id,
                "outline_node_id": draft.outline_node_id,
                "allowed_actions": ["save_and_catalog", "save_only"],
            },
        },
        AssistantTurnDirective.BLOCKED_ON_CATALOGING,
    )


def update_chapter_draft(
    db: Any,
    project_id: str,
    draft_id: str,
    *,
    title: str,
    outline_node_id: str | None,
    content: str,
) -> Any:
    """Persist the editor's current unsaved text before the author saves it."""
    from ...core.exceptions import NotFoundError, ValidationError
    from ...database.models import ChapterDraft

    row = db.query(ChapterDraft).filter(
        ChapterDraft.id == draft_id,
        ChapterDraft.project_id == project_id,
    ).first()
    if not row:
        raise NotFoundError("章节草稿不存在")
    if row.status != "pending":
        raise ValidationError("该章节草稿已经保存或被新草稿替代，不能重复保存")
    row.title = title
    row.outline_node_id = outline_node_id
    row.content = content
    cached = _CHAPTER_DRAFTS.get(draft_id)
    if cached:
        cached.update({"title": title, "outline_node_id": outline_node_id or "", "content": content})
    return row


def mark_chapter_draft_saved(db: Any, draft: Any, chapter_id: str) -> None:
    draft.status = "saved"
    draft.saved_chapter_id = chapter_id
    cached = _CHAPTER_DRAFTS.get(str(draft.id))
    if cached:
        cached["status"] = "saved"
        cached["saved_chapter_id"] = chapter_id


def chapter_draft_result_data(draft: Any) -> dict[str, Any]:
    return {
        "draft_id": str(draft.id),
        "project_id": str(draft.project_id),
        "content_ref": str(draft.id),
        "title": str(draft.title or ""),
        "outline_node_id": draft.outline_node_id,
        "context_manifest_id": draft.context_manifest_id,
        "saved_chapter_id": draft.saved_chapter_id,
        "draft_status": str(draft.status or "pending"),
        "content": str(draft.content or ""),
        "word_count": count_words(str(draft.content or "")),
        "next_actions": ["save_and_catalog", "save_only"],
    }
