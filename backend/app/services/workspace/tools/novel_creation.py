"""Canonical persistence boundary for the conversational novel-creation Agent.

The model owns interviewing, semantic decisions, and structured artifact
authoring.  This module only creates a session and materializes its confirmed
structured snapshot into project records.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session


_CHARACTER_ROLE_TYPES = {
    "protagonist",
    "supporting",
    "antagonist",
    "mentor",
    "other",
}


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _role_type(value: Any, default: str = "supporting") -> str:
    normalized = _text(value).casefold()
    return normalized if normalized in _CHARACTER_ROLE_TYPES else default


def _is_real_session(db: Session) -> bool:
    return db.__class__.__module__.startswith("sqlalchemy.")


def _interview_checklist(
    user_brief: str,
    genre: str,
    target_audience: str,
    platform: str,
) -> dict[str, Any]:
    fields = {
        "genre": {
            "label": "小说类型",
            "description": "作品的题材或类型",
            "provided": bool(genre),
            "value": genre or None,
        },
        "target_audience": {
            "label": "目标读者",
            "description": "作品面向的读者",
            "provided": bool(target_audience),
            "value": target_audience or None,
        },
        "platform": {
            "label": "发布平台",
            "description": "计划发布的平台",
            "provided": bool(platform),
            "value": platform or None,
        },
        "user_brief": {
            "label": "创作方向",
            "description": "作者当前提供的作品构想",
            "provided": bool(user_brief),
            "value": user_brief or None,
        },
    }
    missing = [name for name, item in fields.items() if not item["provided"]]
    return {
        "fields": fields,
        "missing": missing,
        "complete": not missing,
        "next_action": "continue_with_agent",
    }


async def start_novel_creation_session(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create the canonical structured creation session."""
    from app.database.models import NovelCreationSession, PublicPromptPack
    from app.services.novel_creation_workspace import initialize_session_draft, serialize_session
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)
    mode = _text(args.get("mode"), "internal_llm")
    user_brief = _text(args.get("user_brief"))
    target_audience = _text(args.get("target_audience"))
    genre = _text(args.get("genre"))
    platform = _text(args.get("platform"))
    session = NovelCreationSession(
        source_project_id=project_id or None,
        mode=mode,
        user_brief=user_brief or None,
        target_audience=target_audience or None,
        genre=genre or None,
        platform=platform or None,
        status="drafting",
    )
    db.add(session)
    initialize_session_draft(session, args)
    commit_session(db)
    db.refresh(session)

    pack = db.query(PublicPromptPack).filter(
        PublicPromptPack.pack_id == "new_project_setup",
        PublicPromptPack.enabled == True,
    ).first()
    prompt_pack = None
    if pack:
        prompt_pack = {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "title": pack.title,
            "summary": pack.summary,
            "system_prompt": pack.system_prompt,
            "workflow": pack.workflow_json,
            "quality_rubric": pack.quality_rubric_json,
        }
    checklist = _interview_checklist(user_brief, genre, target_audience, platform)
    return {
        "tool": "start_novel_creation_session",
        "status": "ok",
        "detail": f"Session created: {session.id}",
        "data": {
            "session_id": session.id,
            "mode": mode,
            "status": session.status,
            "checklist": checklist,
            "prompt_pack": prompt_pack,
            "missing_fields": checklist["missing"],
            "session": serialize_session(session, include_runs=False),
        },
    }


def _tool_result(status: str, detail: str, data: Any = None) -> dict[str, Any]:
    return {"tool": "finalize_creation_session", "status": status, "detail": detail, "data": data}


def _create_project(db: Session, project_payload: dict[str, Any]):
    from app.database.models import Project

    title = _text(project_payload.get("title"), "未命名小说")[:200]
    style_rules = project_payload.get("style_rules") if isinstance(project_payload.get("style_rules"), list) else []
    forbidden = project_payload.get("forbidden_patterns") if isinstance(project_payload.get("forbidden_patterns"), list) else []
    tags = [value for value in (
        _text(project_payload.get("genre")),
        _text(project_payload.get("subtitle")),
        _text(project_payload.get("platform")),
    ) if value]
    project = Project(
        id=str(uuid4()),
        title=title,
        description=_text(project_payload.get("logline")) or _text(project_payload.get("premise")) or None,
        tags=json.dumps(tags, ensure_ascii=False) if tags else None,
        writing_style=_text(project_payload.get("writing_style"), "natural")[:50],
        narrative_perspective=_text(project_payload.get("narrative_perspective"), "third_person")[:50],
        daily_word_goal=int(project_payload.get("daily_word_goal") or 6000),
        forbidden_sentence_patterns="\n".join(str(item) for item in forbidden) or None,
        rhetoric_guidelines="\n".join(str(item) for item in style_rules) or None,
        custom_style_prompt=_text(project_payload.get("genre_positioning")) or None,
    )
    db.add(project)
    db.flush()
    return project


def _created_manifest(project_id: str, project_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "characters": [],
        "worldbuilding": [],
        "volumes": [],
        "outline": [],
        "relationships": [],
        "worldbuilding_relations": [],
        "warnings": [str(item) for item in project_payload.get("apply_warnings", []) if str(item).strip()],
    }


def _materialize_characters(db: Session, project_id: str, project_payload: dict[str, Any], created: dict[str, Any]):
    from app.database.models import Character

    rows = _objects(project_payload.get("characters"))
    protagonist = project_payload.get("protagonist")
    if isinstance(protagonist, dict) and _text(protagonist.get("name")):
        rows.insert(0, {**protagonist, "role_type": "protagonist"})
    by_name: dict[str, Character] = {}
    for item in rows:
        name = _text(item.get("name"))[:100]
        if not name or name in by_name:
            continue
        character = Character(
            project_id=project_id,
            name=name,
            personality=_text(item.get("personality")) or None,
            background=_text(item.get("background")) or None,
            appearance=_text(item.get("appearance")) or None,
            role_type=_role_type(item.get("role_type")),
            age=_text(item.get("age")) or None,
            life_status=_text(item.get("life_status"), "active")[:50],
            current_location=_text(item.get("current_location")) or None,
            realm_or_level=_text(item.get("realm_or_level")) or None,
            physical_state=_text(item.get("physical_state")) or None,
            mental_state=_text(item.get("mental_state")) or None,
            current_goal=_text(item.get("goal")) or None,
            active_conflict=_text(item.get("conflict")) or None,
            abilities_state=_text(item.get("abilities_state")) or None,
            items_or_assets=_text(item.get("items_or_assets")) or None,
            abilities=json.dumps(item.get("abilities"), ensure_ascii=False) if isinstance(item.get("abilities"), list) else None,
            profile_json=item.get("profile") if isinstance(item.get("profile"), dict) else None,
        )
        db.add(character)
        db.flush()
        by_name[name] = character
        created["characters"].append(name)
    return by_name


def _materialize_worldbuilding(db: Session, project_id: str, project_payload: dict[str, Any], created: dict[str, Any]) -> None:
    from app.database.models import WorldbuildingEntry, WorldbuildingRelation

    by_title: dict[str, WorldbuildingEntry] = {}
    for item in _objects(project_payload.get("worldbuilding")):
        title = _text(item.get("title"))[:200]
        if not title:
            continue
        entry = WorldbuildingEntry(
            project_id=project_id,
            title=title,
            content=_text(item.get("content") or item.get("description"), "待补充"),
            dimension=_text(item.get("dimension"), "culture")[:50],
        )
        db.add(entry)
        db.flush()
        by_title[entry.title] = entry
        created["worldbuilding"].append(entry.title)
    for item in _objects(project_payload.get("worldbuilding_relations")):
        source = by_title.get(_text(item.get("source_title") or item.get("source")))
        target = by_title.get(_text(item.get("target_title") or item.get("target")))
        if not source or not target or source.id == target.id:
            continue
        relation = WorldbuildingRelation(
            project_id=project_id,
            source_entry_id=source.id,
            target_entry_id=target.id,
            relation_type=_text(item.get("relation_type"), "related")[:100],
            description=_text(item.get("description")) or None,
            metadata_json=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
        )
        db.add(relation)
        created["worldbuilding_relations"].append({
            "source": source.title,
            "target": target.title,
            "type": relation.relation_type,
        })


def _materialize_outline(db: Session, project_id: str, project_payload: dict[str, Any], created: dict[str, Any]) -> None:
    from app.database.models import OutlineNode

    volumes: list[OutlineNode] = []
    for index, item in enumerate(_objects(project_payload.get("volume_outline"))):
        title = _text(item.get("title"))[:200]
        if not title:
            continue
        node = OutlineNode(
            project_id=project_id,
            title=title,
            summary=_text(item.get("summary")) or None,
            planned_summary=_text(item.get("planned_summary") or item.get("summary")) or None,
            node_type="volume",
            sort_order=index,
            metadata_json={"start_chapter": item.get("start_chapter"), "end_chapter": item.get("end_chapter")},
        )
        db.add(node)
        db.flush()
        volumes.append(node)
        created["volumes"].append(node.title)
        created["outline"].append(node.title)
    rows = _objects(project_payload.get("outline"))
    ordered = [item for item in rows if _text(item.get("node_type"), "chapter") != "section"]
    ordered += [item for item in rows if _text(item.get("node_type"), "chapter") == "section"]
    by_client_id: dict[str, OutlineNode] = {}
    by_title: dict[str, OutlineNode] = {}
    for index, item in enumerate(ordered):
        node = _outline_node(db, project_id, item, index, volumes, by_client_id, by_title)
        if node is not None:
            created["outline"].append(node.title)


def _outline_node(db, project_id, item, index, volumes, by_client_id, by_title):
    from app.database.models import OutlineNode

    title = _text(item.get("title"))[:200]
    if not title:
        return None
    node_type = _text(item.get("node_type"), "chapter")[:20]
    parent_id = None
    if node_type == "section":
        parent = by_client_id.get(_text(item.get("parent_client_id"))) or by_title.get(_text(item.get("parent_title")))
        parent_id = parent.id if parent else None
    else:
        parent_index = item.get("parent_index")
        if isinstance(parent_index, int) and 0 <= parent_index < len(volumes):
            parent_id = volumes[parent_index].id
    metadata = dict(item.get("metadata")) if isinstance(item.get("metadata"), dict) else {}
    for field in (
        "scene_number", "purpose", "location", "timeline", "pov_character", "characters",
        "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
    ):
        if field in item and field not in metadata:
            metadata[field] = item[field]
    node = OutlineNode(
        project_id=project_id,
        parent_id=parent_id,
        title=title,
        summary=_text(item.get("summary")) or None,
        planned_summary=_text(item.get("planned_summary") or item.get("summary")) or None,
        node_type=node_type,
        sort_order=int(item.get("sort_order") if item.get("sort_order") is not None else index),
        metadata_json=metadata or None,
    )
    db.add(node)
    db.flush()
    client_id = _text(item.get("client_id"))
    if client_id:
        by_client_id[client_id] = node
    by_title[node.title] = node
    return node


def _materialize_relationships(db: Session, project_id: str, project_payload: dict[str, Any], characters, created) -> None:
    from app.database.models import CharacterRelationship

    for item in _objects(project_payload.get("relationships")):
        source = characters.get(_text(item.get("character_a") or item.get("source")))
        target = characters.get(_text(item.get("character_b") or item.get("target")))
        if not source or not target or source.id == target.id:
            continue
        relation = CharacterRelationship(
            project_id=project_id,
            character_a_id=source.id,
            character_b_id=target.id,
            relationship_type=_text(item.get("relationship_type"), "related")[:100],
            description=_text(item.get("description")) or None,
        )
        db.add(relation)
        created["relationships"].append(relation.relationship_type)


def _append_sync_warning(db: Session, sync_job, created: dict[str, Any]) -> None:
    if not _is_real_session(db):
        return
    db.refresh(sync_job)
    if sync_job.status == "completed":
        return
    warning = (
        f"作品已入库，但文件镜像同步失败：{sync_job.last_error or '未知错误'}。请在作品设置中重试。"
        if sync_job.status == "failed"
        else "作品已入库，文件镜像仍在后台排队；司命会自动重试。"
    )
    created["warnings"].append(warning)


async def finalize_creation_session(db: Session, project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Idempotently materialize one structured creation session."""
    from app.database.models import NovelCreationSession, Project
    from app.modules.story.application.content_sync import enqueue_project_sync
    from app.services.novel_creation_workspace import build_project_materialization_payload

    session_id = _text(args.get("session_id"))
    if not session_id:
        return _tool_result("skipped", "session_id is required")
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        return _tool_result("skipped", "Session not found")
    if session.created_project_id:
        existing = db.query(Project).filter(Project.id == session.created_project_id).first()
        if existing:
            return _tool_result("ok", f"Project already created: {existing.title}", {
                "project_id": existing.id, "idempotent": True, "warnings": [],
            })
    if not isinstance(session.draft_json, dict) or int(session.schema_version or 0) < 2:
        return _tool_result("skipped", "Creation session has no structured artifact snapshot")
    project_payload = build_project_materialization_payload(session)
    if not isinstance(project_payload, dict):
        return _tool_result("skipped", "Creation snapshot is invalid")
    try:
        project = _create_project(db, project_payload)
        created = _created_manifest(project.id, project_payload)
        characters = _materialize_characters(db, project.id, project_payload, created)
        _materialize_worldbuilding(db, project.id, project_payload, created)
        _materialize_outline(db, project.id, project_payload, created)
        _materialize_relationships(db, project.id, project_payload, characters, created)
        session.created_project_id = project.id
        session.status = "completed"
        session.completed_at = datetime.utcnow()
        sync_job = enqueue_project_sync(db, project.id, source="novel_creation")
        commit_session(db)
        _append_sync_warning(db, sync_job, created)
        detail = (
            f"Project created: {project.title} ({len(created['characters'])} characters, "
            f"{len(created['worldbuilding'])} worldbuilding entries, {len(created['outline'])} outline nodes)"
        )
        return _tool_result("ok", detail, created)
    except Exception as exc:
        db.rollback()
        session.status = "failed"
        commit_session(db)
        return _tool_result("error", f"Failed to finalize creation session: {exc}")
