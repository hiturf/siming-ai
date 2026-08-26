"""Deterministic submission of one structured creation artifact."""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.modules.creation.interfaces.session_dependencies import novel_creation_session_store
from app.services.novel_creation_confirmation import assess_creation_confirmation, save_exact_confirmation
from app.services.novel_creation_workspace import STAGE_LABELS, STAGE_ORDER, derive_stage, save_stage, serialize_session


def _text(value: Any) -> str:
    return str(value or "").strip()


async def save_creation_stage_data(
    db: Session,
    args: dict[str, Any],
    *,
    normalize_stage: Callable[[str, dict[str, Any]], dict[str, Any]],
    validate_stage: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    session_id, stage = _text(args.get("session_id")), _text(args.get("stage"))
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        return {"tool": "save_creation_artifact", "status": "skipped", "detail": "Session not found", "data": None}
    if stage not in STAGE_ORDER:
        return {"tool": "save_creation_artifact", "status": "skipped", "detail": "Unknown stage", "data": None}
    confirmation = assess_creation_confirmation(
        session,
        stage,
        requested_data=args.get("data"),
        confirm=bool(args.get("confirm", True)),
    )
    if confirmation.action == "already_confirmed":
        return {
            "tool": "save_creation_artifact",
            "status": "ok",
            "detail": f"{STAGE_LABELS[stage]}已经确认",
            "data": serialize_session(session),
        }
    expected_revision = args.get("expected_revision")
    if expected_revision is not None and int(session.revision or 0) != int(expected_revision):
        return {
            "tool": "save_creation_artifact",
            "status": "error",
            "detail": "Novel creation session revision conflict",
            "data": {
                "failure_class": "revision_conflict",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        }
    if confirmation.action == "confirm_exact":
        save_exact_confirmation(session, stage, confirmation, source=_text(args.get("source")) or "author")
        commit_session(db)
        return {
            "tool": "save_creation_artifact",
            "status": "ok",
            "detail": f"{STAGE_LABELS[stage]}已确认",
            "data": serialize_session(session),
        }
    data = args.get("data")
    if not isinstance(data, dict):
        data = derive_stage(session, stage)
    try:
        data = normalize_stage(stage, data)
        validate_stage(stage, data)
        save_stage(
            session,
            stage,
            data,
            confirm=bool(args.get("confirm", True)),
            source=_text(args.get("source")) or "author",
        )
        commit_session(db)
        return {
            "tool": "save_creation_artifact",
            "status": "ok",
            "detail": f"{STAGE_LABELS[stage]}已保存",
            "data": serialize_session(session),
        }
    except Exception as exc:
        db.rollback()
        return {"tool": "save_creation_artifact", "status": "error", "detail": str(exc), "data": None}
