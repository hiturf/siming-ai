"""Rollback chapter-derived cataloging projections after semantic edits or deletion.

Cataloging is an append-only audit process, but its author-facing tables are a
projection of the currently accepted chapter sequence.  Removing or
semantically rewriting a chapter invalidates that projection from the changed
chapter onward.  This module restores logged values in reverse application
order, removes chapter-owned rows, and marks the surviving suffix for
re-cataloging.

The delete listener is deliberately installed on ``Session.before_flush`` so
HTTP, Workspace/MCP and Gateway/mobile tombstones all enter the same rollback
boundary before a ``Chapter`` row disappears.  Whole-project deletion is
excluded because all project-owned data is already removed together.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import event, or_
from sqlalchemy.orm import Session

from app.modules.assistant.infrastructure.models import RagChunk, RagDocument
from app.modules.continuity.infrastructure.models import (
    CatalogingApplyLog,
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
    CausalEdge,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSummary,
    CharacterChangeLog,
    CharacterNarrativeState,
    CharacterTimeline,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
    NarrativeGovernanceEvent,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterCharacter,
    ChapterWorldbuilding,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterRelationship,
    CharacterVersion,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    WorldbuildingEntry,
    WorldbuildingRelation,
)

from .snapshots import character_snapshot, outline_snapshot, worldbuilding_snapshot

_LISTENER_INSTALLED = False
_DELETE_GUARD = "siming.chapter_cataloging_delete_rollback"
_DELETE_RESULTS = "siming.chapter_cataloging_delete_results"
_UNSUPPORTED = object()


def _json_value(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _same(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _ordered_project_chapters(db: Session, project_id: str) -> list[Chapter]:
    return (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
        .all()
    )


def chapter_suffix_ids(db: Session, project_id: str, chapter_id: str) -> list[str]:
    """Return the changed chapter and every later chapter in canonical order."""

    chapters = _ordered_project_chapters(db, project_id)
    for index, chapter in enumerate(chapters):
        if chapter.id == chapter_id:
            return [item.id for item in chapters[index:]]
    return []


def _delete_rows(rows: list[Any]) -> int:
    count = 0
    for row in rows:
        session = Session.object_session(row)
        if session is not None:
            session.delete(row)
            count += 1
    return count


def _character_ai_snapshot(character: Character) -> dict[str, Any] | None:
    config = character.ai_config
    if not config:
        return None
    phrases: list[str] = []
    if config.catchphrases:
        try:
            parsed = json.loads(config.catchphrases)
            if isinstance(parsed, list):
                phrases = [str(item) for item in parsed]
        except (TypeError, ValueError):
            phrases = []
    return {
        "tone_style": config.tone_style or "",
        "catchphrases": phrases,
        "verbosity": config.verbosity or "",
        "emotion_tendency": config.emotion_tendency or "",
        "model_override": config.model_override or "",
        "custom_system_prompt": config.custom_system_prompt or "",
    }


def _restore_character(
    db: Session,
    character: Character,
    snapshot: dict[str, Any],
    affected_ids: set[str],
) -> None:
    for field in (
        "name",
        "appearance",
        "personality",
        "background",
        "role_type",
        "age",
        "life_status",
        "current_location",
        "realm_or_level",
        "physical_state",
        "mental_state",
        "current_goal",
        "active_conflict",
        "abilities_state",
        "items_or_assets",
    ):
        if field in snapshot:
            setattr(character, field, snapshot.get(field))

    abilities = snapshot.get("abilities")
    if isinstance(abilities, list):
        character.abilities = json.dumps([str(item) for item in abilities], ensure_ascii=False)
    elif abilities is not None:
        character.abilities = str(abilities)

    profile = snapshot.get("profile")
    if isinstance(profile, dict):
        character.profile_json = dict(profile)

    desired_aliases = {
        str(item).strip()
        for item in (snapshot.get("aliases") or [])
        if str(item).strip() and str(item).strip() != character.name
    }
    existing = list(
        db.query(CharacterAlias).filter(CharacterAlias.character_id == character.id).all()
    )
    existing_by_name = {row.alias: row for row in existing}
    for row in existing:
        if row.alias in desired_aliases:
            continue
        if row.source_chapter_id in affected_ids:
            db.delete(row)
    for alias in desired_aliases:
        if alias not in existing_by_name:
            db.add(
                CharacterAlias(
                    project_id=character.project_id,
                    character_id=character.id,
                    alias=alias[:200],
                    alias_type="alias",
                )
            )

    ai_snapshot = snapshot.get("ai_config")
    config = character.ai_config or db.query(CharacterAIConfig).filter(
        CharacterAIConfig.character_id == character.id
    ).first()
    if isinstance(ai_snapshot, dict):
        if config is None:
            config = CharacterAIConfig(character_id=character.id)
            db.add(config)
            character.ai_config = config
        config.tone_style = str(ai_snapshot.get("tone_style") or "neutral")[:100]
        config.catchphrases = json.dumps(
            [str(item) for item in (ai_snapshot.get("catchphrases") or [])],
            ensure_ascii=False,
        )
        config.verbosity = str(ai_snapshot.get("verbosity") or "moderate")[:50]
        config.emotion_tendency = str(
            ai_snapshot.get("emotion_tendency") or "neutral"
        )[:100]
        config.model_override = str(ai_snapshot.get("model_override") or "")[:200] or None
        config.custom_system_prompt = (
            str(ai_snapshot.get("custom_system_prompt") or "")[:12000] or None
        )
    elif config is not None:
        db.delete(config)
    character.updated_at = datetime.utcnow()


def _restore_worldbuilding(entry: WorldbuildingEntry, snapshot: dict[str, Any]) -> None:
    for field in ("dimension", "title", "content", "status", "confidence"):
        if field in snapshot:
            setattr(entry, field, snapshot.get(field))
    entry.updated_at = datetime.utcnow()


def _restore_outline(node: OutlineNode, snapshot: dict[str, Any]) -> None:
    for field in (
        "title",
        "node_type",
        "parent_id",
        "summary",
        "status",
        "source_chapter_id",
        "actual_summary",
        "planned_summary",
    ):
        if field in snapshot:
            setattr(node, field, snapshot.get(field))
    if not node.source_chapter_id:
        node.cataloging_status = None
    node.updated_at = datetime.utcnow()


def _relationship_snapshot(db: Session, row: CharacterRelationship) -> dict[str, Any]:
    source = db.get(Character, row.character_a_id)
    target = db.get(Character, row.character_b_id)
    return {
        "source_name": source.name if source else row.character_a_id,
        "target_name": target.name if target else row.character_b_id,
        "relationship_type": row.relationship_type,
        "description": row.description,
    }


def _timeline_snapshot(row: Any) -> dict[str, Any]:
    if isinstance(row, CharacterTimeline):
        return {
            "event_description": row.event_description,
            "event_type": row.event_type,
            "emotional_state_change": row.emotional_state_change,
            "sort_order": row.sort_order,
        }
    return {
        "event_description": row.event_description,
        "event_type": row.event_type,
        "evidence": row.evidence,
        "sort_order": row.sort_order,
    }


def _current_projection(
    db: Session,
    target_type: str,
    target_id: str,
    expected_new: Any,
) -> Any:
    if target_type == "character":
        if isinstance(expected_new, dict) and {
            "primary",
            "secondary",
        } <= set(expected_new):
            primary_id = str((expected_new.get("primary") or {}).get("id") or target_id)
            secondary_id = str((expected_new.get("secondary") or {}).get("id") or "")
            primary = db.get(Character, primary_id)
            secondary = db.get(Character, secondary_id) if secondary_id else None
            return {
                "primary": character_snapshot(primary),
                "secondary": character_snapshot(secondary),
            }
        return character_snapshot(db.get(Character, target_id))
    if target_type == "worldbuilding":
        return worldbuilding_snapshot(db.get(WorldbuildingEntry, target_id))
    if target_type == "outline_node":
        return outline_snapshot(db.get(OutlineNode, target_id))
    if target_type == "character_relationship":
        row = db.get(CharacterRelationship, target_id)
        return _relationship_snapshot(db, row) if row else None
    if target_type in {"character_timeline", "worldbuilding_timeline"}:
        model = CharacterTimeline if target_type == "character_timeline" else WorldbuildingTimeline
        row = db.get(model, target_id)
        return _timeline_snapshot(row) if row else None
    if target_type in {"chapter_summary", "chapter", "cataloging_fact"}:
        return _UNSUPPORTED
    return _UNSUPPORTED


def _character_has_external_ownership(
    db: Session,
    character_id: str,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    rollback_relationship_ids: set[str],
) -> bool:
    if db.query(ChapterCharacter).filter(
        ChapterCharacter.character_id == character_id,
        ChapterCharacter.chapter_id.notin_(affected_ids),
    ).first():
        return True
    if db.query(CharacterVersion).filter(
        CharacterVersion.character_id == character_id,
        or_(
            CharacterVersion.source_chapter_id.is_(None),
            CharacterVersion.source_chapter_id.notin_(affected_ids),
        ),
    ).first():
        return True
    if db.query(CharacterAlias).filter(
        CharacterAlias.character_id == character_id,
        or_(
            CharacterAlias.source_chapter_id.is_(None),
            CharacterAlias.source_chapter_id.notin_(affected_ids),
        ),
    ).first():
        return True
    relationships = db.query(CharacterRelationship).filter(
        or_(
            CharacterRelationship.character_a_id == character_id,
            CharacterRelationship.character_b_id == character_id,
        )
    ).all()
    if any(row.id not in rollback_relationship_ids for row in relationships):
        return True
    links = (
        db.query(OutlineNodeCharacter)
        .filter(OutlineNodeCharacter.character_id == character_id)
        .all()
    )
    if any(
        not (
            row.role_in_scene == "建档关联"
            and row.outline_node_id in affected_outline_ids
        )
        for row in links
    ):
        return True
    if db.query(CharacterNarrativeState).filter(
        CharacterNarrativeState.character_id == character_id,
        or_(
            CharacterNarrativeState.chapter_id.is_(None),
            CharacterNarrativeState.chapter_id.notin_(affected_ids),
        ),
    ).first():
        return True
    return False


def _world_has_external_ownership(
    db: Session,
    entry_id: str,
    affected_ids: set[str],
) -> bool:
    if db.query(ChapterWorldbuilding).filter(
        ChapterWorldbuilding.worldbuilding_entry_id == entry_id,
        ChapterWorldbuilding.chapter_id.notin_(affected_ids),
    ).first():
        return True
    if db.query(WorldbuildingVersion).filter(
        WorldbuildingVersion.entry_id == entry_id,
        or_(
            WorldbuildingVersion.source_chapter_id.is_(None),
            WorldbuildingVersion.source_chapter_id.notin_(affected_ids),
        ),
    ).first():
        return True
    if db.query(WorldbuildingRelation).filter(
        or_(
            WorldbuildingRelation.source_entry_id == entry_id,
            WorldbuildingRelation.target_entry_id == entry_id,
        )
    ).first():
        return True
    return False


def _undo_apply_log(
    db: Session,
    log: CatalogingApplyLog,
    candidate: CatalogingCandidate,
    old_value: Any,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    rollback_relationship_ids: set[str],
    result: dict[str, Any],
) -> None:
    target_type = str(log.target_type or candidate.target_type or "")
    target_id = str(log.target_id or candidate.target_id or "")
    if not target_id:
        return

    if target_type == "character":
        if isinstance(old_value, dict) and {
            "primary",
            "secondary",
        } <= set(old_value):
            for key in ("primary", "secondary"):
                snapshot = old_value.get(key)
                if not isinstance(snapshot, dict):
                    continue
                character = db.get(Character, str(snapshot.get("id") or ""))
                if character:
                    _restore_character(db, character, snapshot, affected_ids)
                    result["restored_characters"] += 1
            result["warnings"].append(
                "角色合并已恢复角色卡字段；旧应用日志不含合并前全部关联归属，相关角色关系需复核"
            )
            return
        character = db.get(Character, target_id)
        if old_value is None:
            if character is None:
                return
            if _character_has_external_ownership(
                db,
                target_id,
                affected_ids,
                affected_outline_ids,
                rollback_relationship_ids,
            ):
                result["warnings"].append(
                    f"新角色 {character.name} 已被建档范围外的数据使用，未自动删除"
                )
                result["preserved_entities"].append(target_id)
                return
            db.delete(character)
            result["deleted_characters"] += 1
            result["deleted_character_ids"].append(target_id)
            return
        if character and isinstance(old_value, dict):
            _restore_character(db, character, old_value, affected_ids)
            result["restored_characters"] += 1
        return

    if target_type == "worldbuilding":
        entry = db.get(WorldbuildingEntry, target_id)
        if old_value is None:
            if entry is None:
                return
            if _world_has_external_ownership(db, target_id, affected_ids):
                result["warnings"].append(
                    f"新世界观“{entry.title}”已被建档范围外的数据使用，未自动删除"
                )
                result["preserved_entities"].append(target_id)
                return
            db.delete(entry)
            result["deleted_worldbuilding"] += 1
            result["deleted_worldbuilding_ids"].append(target_id)
            return
        if entry and isinstance(old_value, dict):
            _restore_worldbuilding(entry, old_value)
            result["restored_worldbuilding"] += 1
        return

    if target_type == "character_relationship":
        row = db.get(CharacterRelationship, target_id)
        if old_value is None:
            if row:
                db.delete(row)
                result["deleted_relationships"] += 1
        elif row and isinstance(old_value, dict):
            row.relationship_type = str(old_value.get("relationship_type") or row.relationship_type)[:100]
            row.description = old_value.get("description")
            result["restored_relationships"] += 1
        return

    if target_type in {"character_timeline", "worldbuilding_timeline"}:
        model = CharacterTimeline if target_type == "character_timeline" else WorldbuildingTimeline
        row = db.get(model, target_id)
        if old_value is None:
            if row:
                db.delete(row)
        elif row and isinstance(old_value, dict):
            row.event_description = str(old_value.get("event_description") or "")
            row.event_type = str(old_value.get("event_type") or row.event_type)[:50]
            row.sort_order = int(old_value.get("sort_order") or 0)
            if isinstance(row, CharacterTimeline):
                row.emotional_state_change = old_value.get("emotional_state_change")
            else:
                row.evidence = old_value.get("evidence")
        result["restored_timeline_rows"] += 1
        return

    if target_type == "outline_node":
        node = db.get(OutlineNode, target_id)
        if old_value is None:
            if node and node.source_chapter_id in affected_ids and node.cataloging_status == "cataloged":
                db.delete(node)
                result["deleted_outline_nodes"] += 1
        elif node and isinstance(old_value, dict):
            _restore_outline(node, old_value)
            result["restored_outline_nodes"] += 1


def _rollback_apply_logs(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    result: dict[str, Any],
) -> None:
    rows = (
        db.query(CatalogingApplyLog, CatalogingCandidate)
        .join(CatalogingCandidate, CatalogingCandidate.id == CatalogingApplyLog.candidate_id)
        .filter(
            CatalogingCandidate.project_id == project_id,
            CatalogingCandidate.chapter_id.in_(affected_ids),
        )
        .order_by(CatalogingApplyLog.applied_at.asc(), CatalogingApplyLog.id.asc())
        .all()
    )
    groups: dict[tuple[str, str], list[tuple[CatalogingApplyLog, CatalogingCandidate]]] = defaultdict(list)
    for log, candidate in rows:
        target_type = str(log.target_type or candidate.target_type or "")
        target_id = str(log.target_id or candidate.target_id or "")
        if target_type and target_id:
            groups[(target_type, target_id)].append((log, candidate))

    rollback_relationship_ids = {
        target_id
        for (target_type, target_id) in groups
        if target_type == "character_relationship"
    }
    for (target_type, target_id), group in groups.items():
        latest_log, _latest_candidate = group[-1]
        latest_new = _json_value(latest_log.new_value)
        current = _current_projection(db, target_type, target_id, latest_new)
        if current is _UNSUPPORTED:
            continue
        if not _same(current, latest_new):
            result["warnings"].append(
                f"{target_type}:{target_id} 在建档后又被作者或其他流程修改，已保留当前值"
            )
            result["preserved_entities"].append(target_id)
            continue
        for log, candidate in reversed(group):
            _undo_apply_log(
                db,
                log,
                candidate,
                _json_value(log.old_value),
                affected_ids,
                affected_outline_ids,
                rollback_relationship_ids,
                result,
            )
            result["rolled_back_apply_logs"] += 1


def _rollback_legacy_character_changes(
    db: Session,
    affected_ids: set[str],
    result: dict[str, Any],
) -> None:
    rows = (
        db.query(CharacterChangeLog)
        .filter(
            CharacterChangeLog.chapter_id.in_(affected_ids),
            CharacterChangeLog.confirmed.is_(True),
        )
        .order_by(CharacterChangeLog.created_at.desc(), CharacterChangeLog.id.desc())
        .all()
    )
    supported = {
        "appearance",
        "personality",
        "background",
        "abilities",
    }
    for row in rows:
        if row.field_name not in supported:
            continue
        character = db.get(Character, row.character_id)
        if not character:
            continue
        current = getattr(character, row.field_name)
        if row.new_value is not None and str(current or "") != str(row.new_value or ""):
            continue
        setattr(character, row.field_name, row.old_value)
        result["legacy_character_changes_reverted"] += 1


def _catalog_node_depth(node: OutlineNode) -> int:
    depth = 0
    current = node.parent
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        depth += 1
        current = current.parent
    return depth


def _catalog_node_safe_to_delete(
    db: Session,
    node: OutlineNode,
    affected_ids: set[str],
) -> bool:
    if node.source_chapter_id not in affected_ids or node.cataloging_status != "cataloged":
        return False
    if db.query(Chapter).filter(
        Chapter.outline_node_id == node.id,
        Chapter.id.notin_(affected_ids),
    ).first():
        return False
    return all(
        child.source_chapter_id in affected_ids and child.cataloging_status == "cataloged"
        for child in node.children
    )


def _cleanup_chapter_owned_rows(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    result: dict[str, Any],
) -> None:
    for key, model in (
        ("chapter_summaries", ChapterSummary),
        ("chapter_character_links", ChapterCharacter),
        ("chapter_worldbuilding_links", ChapterWorldbuilding),
        ("character_timeline_rows", CharacterTimeline),
        ("worldbuilding_timeline_rows", WorldbuildingTimeline),
        ("character_narrative_states", CharacterNarrativeState),
        ("chapter_quality_metrics", ChapterQualityMetric),
        ("chapter_governance_reviews", ChapterGovernanceReview),
        ("chapter_checkpoints", NarrativeCheckpoint),
    ):
        column = getattr(model, "chapter_id")
        rows = db.query(model).filter(column.in_(affected_ids)).all()
        result["removed_rows"][key] = _delete_rows(rows)

    alias_rows = db.query(CharacterAlias).filter(
        CharacterAlias.source_chapter_id.in_(affected_ids)
    ).all()
    result["removed_rows"]["character_aliases"] = _delete_rows(alias_rows)

    character_versions = db.query(CharacterVersion).filter(
        CharacterVersion.source_chapter_id.in_(affected_ids)
    ).all()
    result["removed_rows"]["character_versions"] = _delete_rows(character_versions)
    world_versions = db.query(WorldbuildingVersion).filter(
        WorldbuildingVersion.source_chapter_id.in_(affected_ids)
    ).all()
    result["removed_rows"]["worldbuilding_versions"] = _delete_rows(world_versions)

    outline_links = db.query(OutlineNodeCharacter).filter(
        OutlineNodeCharacter.outline_node_id.in_(affected_outline_ids),
        OutlineNodeCharacter.role_in_scene == "建档关联",
    ).all()
    result["removed_rows"]["outline_character_links"] = _delete_rows(outline_links)

    catalog_nodes = db.query(OutlineNode).filter(
        OutlineNode.project_id == project_id,
        OutlineNode.source_chapter_id.in_(affected_ids),
        OutlineNode.cataloging_status == "cataloged",
    ).all()
    for node in sorted(catalog_nodes, key=_catalog_node_depth, reverse=True):
        if node in db.deleted:
            continue
        if _catalog_node_safe_to_delete(db, node, affected_ids):
            for chapter in _ordered_project_chapters(db, project_id):
                if chapter.outline_node_id == node.id:
                    chapter.outline_node_id = None
            db.delete(node)
            result["deleted_outline_nodes"] += 1
        else:
            node.source_chapter_id = None
            node.cataloging_status = None
            result["warnings"].append(
                f"建档大纲节点“{node.title}”已有范围外引用，已保留并解除建档归属"
            )

    facts = db.query(CatalogingFact).filter(
        CatalogingFact.project_id == project_id,
        CatalogingFact.chapter_id.in_(affected_ids),
    ).all()
    for fact in facts:
        fact.status = "superseded"
    result["removed_rows"]["cataloging_facts_superseded"] = len(facts)


def _prior_governance_status(
    db: Session,
    project_id: str,
    item_id: str,
    affected_ids: set[str],
) -> str | None:
    event_row = (
        db.query(NarrativeGovernanceEvent)
        .filter(
            NarrativeGovernanceEvent.project_id == project_id,
            NarrativeGovernanceEvent.item_id == item_id,
            NarrativeGovernanceEvent.chapter_id.in_(affected_ids),
        )
        .order_by(NarrativeGovernanceEvent.created_at.asc(), NarrativeGovernanceEvent.id.asc())
        .first()
    )
    return str(event_row.from_status) if event_row and event_row.from_status else None


def _rollback_governance(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    deleted_character_ids: set[str],
    reason: str,
    result: dict[str, Any],
) -> None:
    for model in (Foreshadowing, CausalEdge, NarrativeDebt):
        rows = db.query(model).filter(model.project_id == project_id).all()
        for row in rows:
            linked = any(
                getattr(row, field, None) in affected_ids
                for field in ("source_chapter_id", "target_chapter_id", "resolved_chapter_id")
            )
            if not linked:
                if isinstance(row, CausalEdge) and deleted_character_ids:
                    row.character_ids = [
                        item
                        for item in (row.character_ids or [])
                        if str(item) not in deleted_character_ids
                    ]
                continue
            if (
                getattr(row, "source", None) == "cataloging"
                and getattr(row, "source_chapter_id", None) in affected_ids
            ):
                events = db.query(NarrativeGovernanceEvent).filter(
                    NarrativeGovernanceEvent.project_id == project_id,
                    NarrativeGovernanceEvent.item_id == row.id,
                ).all()
                _delete_rows(events)
                db.delete(row)
                result["deleted_governance_items"] += 1
                continue

            prior = _prior_governance_status(db, project_id, row.id, affected_ids)
            if prior:
                row.status = prior
            elif getattr(row, "resolved_chapter_id", None) in affected_ids:
                row.status = "open"
            if getattr(row, "source_chapter_id", None) in affected_ids:
                row.source_chapter_id = None
            if hasattr(row, "target_chapter_id") and row.target_chapter_id in affected_ids:
                row.target_chapter_id = None
            if getattr(row, "resolved_chapter_id", None) in affected_ids:
                row.resolved_chapter_id = None
                if hasattr(row, "resolved_chapter_version"):
                    row.resolved_chapter_version = None
                if hasattr(row, "resolution_note"):
                    row.resolution_note = None
                if hasattr(row, "resolution_evidence"):
                    row.resolution_evidence = None
                if hasattr(row, "verification_note"):
                    row.verification_note = None
                if hasattr(row, "verified_at"):
                    row.verified_at = None
                if hasattr(row, "closed_by"):
                    row.closed_by = None
            if hasattr(row, "stale_reason"):
                row.stale_reason = reason[:4000]
            if isinstance(row, CausalEdge) and deleted_character_ids:
                row.character_ids = [
                    item
                    for item in (row.character_ids or [])
                    if str(item) not in deleted_character_ids
                ]
            result["restored_governance_items"] += 1


def _restore_ledger_projection(
    db: Session,
    project_id: str,
    affected_ids: set[str],
) -> int:
    rows = (
        db.query(CatalogingFact)
        .filter(
            CatalogingFact.project_id == project_id,
            CatalogingFact.fact_type == "narrative_ledger_entry",
            CatalogingFact.chapter_id.notin_(affected_ids),
        )
        .order_by(CatalogingFact.created_at.asc(), CatalogingFact.id.asc())
        .all()
    )
    grouped: dict[tuple[str, str], list[CatalogingFact]] = defaultdict(list)
    for row in rows:
        payload = _json_value(row.raw_payload)
        if not isinstance(payload, dict):
            continue
        key = (
            str(payload.get("ledger_type") or "event"),
            str(payload.get("ledger_key") or ""),
        )
        if key[1]:
            grouped[key].append(row)
    restored = 0
    for items in grouped.values():
        for row in items[:-1]:
            row.status = "superseded"
        items[-1].status = "active"
        restored += 1
    return restored


def _refresh_character_provenance(
    db: Session,
    project_id: str,
    character_ids: set[str],
) -> None:
    for character_id in character_ids:
        character = db.get(Character, character_id)
        if not character or character in db.deleted:
            continue
        latest_seen = (
            db.query(Chapter)
            .join(ChapterCharacter, ChapterCharacter.chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                ChapterCharacter.character_id == character_id,
            )
            .order_by(Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())
            .first()
        )
        latest_updated = (
            db.query(Chapter)
            .join(CharacterVersion, CharacterVersion.source_chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                CharacterVersion.character_id == character_id,
            )
            .order_by(Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())
            .first()
        )
        character.last_seen_chapter_id = latest_seen.id if latest_seen else None
        character.last_updated_chapter_id = latest_updated.id if latest_updated else None
        versions = db.query(CharacterVersion.version_number).filter(
            CharacterVersion.character_id == character_id
        ).all()
        character.current_version = max((int(item[0] or 1) for item in versions), default=1)


def _refresh_worldbuilding_provenance(
    db: Session,
    project_id: str,
    entry_ids: set[str],
) -> None:
    for entry_id in entry_ids:
        entry = db.get(WorldbuildingEntry, entry_id)
        if not entry or entry in db.deleted:
            continue
        linked = (
            db.query(Chapter)
            .join(ChapterWorldbuilding, ChapterWorldbuilding.chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                ChapterWorldbuilding.worldbuilding_entry_id == entry_id,
            )
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
            .all()
        )
        version_chapters = (
            db.query(Chapter)
            .join(WorldbuildingVersion, WorldbuildingVersion.source_chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                WorldbuildingVersion.entry_id == entry_id,
            )
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
            .all()
        )
        candidates = linked or version_chapters
        entry.first_seen_chapter_id = candidates[0].id if candidates else None
        entry.last_updated_chapter_id = version_chapters[-1].id if version_chapters else None


def _invalidate_cataloging_audit(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    reason: str,
    result: dict[str, Any],
) -> None:
    candidates = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.project_id == project_id,
        CatalogingCandidate.chapter_id.in_(affected_ids),
    ).all()
    for candidate in candidates:
        candidate.status = "rejected"
        candidate.error = reason[:2000]
        candidate.updated_at = datetime.utcnow()
    result["invalidated_candidates"] = len(candidates)

    runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.project_id == project_id,
        CatalogingChapterRun.chapter_id.in_(affected_ids),
    ).all()
    job_ids: set[str] = set()
    for run in runs:
        run.status = "skipped_by_user"
        run.error = reason[:2000]
        run.review_warning = "章节顺序或语义已变化，旧建档投影已回退"
        run.completed_at = datetime.utcnow()
        job_ids.add(run.job_id)
    result["invalidated_runs"] = len(runs)

    jobs = db.query(CatalogingJob).filter(CatalogingJob.id.in_(job_ids)).all() if job_ids else []
    for job in jobs:
        job.status = "cancelled"
        job.context_integrity = "stale"
        job.error = reason[:2000]
        job.current_chapter_id = None
        job.blocked_chapter_id = None
        job.completed_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
    result["cancelled_jobs"] = len(jobs)


def _clear_rag_projection(
    db: Session,
    project_id: str,
    chapter_ids: set[str],
    character_ids: set[str],
    world_ids: set[str],
    outline_ids: set[str],
) -> int:
    source_pairs = {
        *(('chapter_summary', item) for item in chapter_ids),
        *(('character', item) for item in character_ids),
        *(('character_timeline', item) for item in character_ids),
        *(('worldbuilding', item) for item in world_ids),
        *(('outline', item) for item in outline_ids),
    }
    documents = db.query(RagDocument).filter(RagDocument.project_id == project_id).all()
    doomed = [row for row in documents if (row.source_type, row.source_id) in source_pairs]
    document_ids = {row.id for row in doomed}
    chunks = db.query(RagChunk).filter(RagChunk.document_id.in_(document_ids)).all() if document_ids else []
    _delete_rows(chunks)
    _delete_rows(doomed)
    return len(doomed)


def rollback_cataloging_from_chapter(
    db: Session,
    project_id: str,
    chapter_id: str,
    *,
    reason: str,
    deleting_chapter: bool = False,
) -> dict[str, Any]:
    """Restore the current projection to immediately before one chapter's cataloging.

    The trigger chapter and all later chapters are invalidated together.  When
    deleting, only the later surviving chapters are marked for re-cataloging;
    on a semantic edit, the trigger chapter remains and is marked as well.
    """

    ordered = _ordered_project_chapters(db, project_id)
    trigger_index = next(
        (index for index, item in enumerate(ordered) if item.id == chapter_id),
        None,
    )
    if trigger_index is None:
        return {
            "affected_chapter_ids": [],
            "recatalog_required_chapter_ids": [],
            "warnings": [],
        }
    affected = ordered[trigger_index:]
    affected_ids = {item.id for item in affected}
    recatalog = affected[1:] if deleting_chapter else affected
    affected_outline_ids = {
        str(item.outline_node_id)
        for item in affected
        if item.outline_node_id
    }

    result: dict[str, Any] = {
        "trigger_chapter_id": chapter_id,
        "affected_chapter_ids": [item.id for item in affected],
        "recatalog_required_chapter_ids": [item.id for item in recatalog],
        "rolled_back_apply_logs": 0,
        "invalidated_candidates": 0,
        "invalidated_runs": 0,
        "cancelled_jobs": 0,
        "deleted_characters": 0,
        "deleted_character_ids": [],
        "restored_characters": 0,
        "deleted_worldbuilding": 0,
        "deleted_worldbuilding_ids": [],
        "restored_worldbuilding": 0,
        "deleted_relationships": 0,
        "restored_relationships": 0,
        "restored_timeline_rows": 0,
        "deleted_outline_nodes": 0,
        "restored_outline_nodes": 0,
        "deleted_governance_items": 0,
        "restored_governance_items": 0,
        "legacy_character_changes_reverted": 0,
        "ledger_keys_restored": 0,
        "rag_documents_invalidated": 0,
        "removed_rows": {},
        "preserved_entities": [],
        "warnings": [],
    }

    apply_rows = (
        db.query(CatalogingApplyLog, CatalogingCandidate)
        .join(CatalogingCandidate, CatalogingCandidate.id == CatalogingApplyLog.candidate_id)
        .filter(
            CatalogingCandidate.project_id == project_id,
            CatalogingCandidate.chapter_id.in_(affected_ids),
        )
        .all()
    )
    affected_character_ids = {
        str(log.target_id or candidate.target_id)
        for log, candidate in apply_rows
        if str(log.target_type or candidate.target_type or "") == "character"
        and (log.target_id or candidate.target_id)
    }
    affected_world_ids = {
        str(log.target_id or candidate.target_id)
        for log, candidate in apply_rows
        if str(log.target_type or candidate.target_type or "") == "worldbuilding"
        and (log.target_id or candidate.target_id)
    }
    affected_outline_ids.update(
        str(log.target_id or candidate.target_id)
        for log, candidate in apply_rows
        if str(log.target_type or candidate.target_type or "") == "outline_node"
        and (log.target_id or candidate.target_id)
    )

    _rollback_apply_logs(
        db,
        project_id,
        affected_ids,
        affected_outline_ids,
        result,
    )
    _rollback_legacy_character_changes(db, affected_ids, result)
    _cleanup_chapter_owned_rows(
        db,
        project_id,
        affected_ids,
        affected_outline_ids,
        result,
    )
    deleted_character_ids = set(result["deleted_character_ids"])
    _rollback_governance(
        db,
        project_id,
        affected_ids,
        deleted_character_ids,
        reason,
        result,
    )
    result["ledger_keys_restored"] = _restore_ledger_projection(
        db,
        project_id,
        affected_ids,
    )
    _refresh_character_provenance(
        db,
        project_id,
        affected_character_ids - deleted_character_ids,
    )
    _refresh_worldbuilding_provenance(
        db,
        project_id,
        affected_world_ids - set(result["deleted_worldbuilding_ids"]),
    )
    _invalidate_cataloging_audit(db, project_id, affected_ids, reason, result)
    result["rag_documents_invalidated"] = _clear_rag_projection(
        db,
        project_id,
        affected_ids,
        affected_character_ids,
        affected_world_ids,
        affected_outline_ids,
    )

    for chapter in recatalog:
        chapter.cataloging_required = bool((chapter.content or "").strip())

    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["preserved_entities"] = list(dict.fromkeys(result["preserved_entities"]))
    return result


def pop_delete_rollback_result(db: Session, chapter_id: str) -> dict[str, Any] | None:
    results = db.info.get(_DELETE_RESULTS)
    if not isinstance(results, dict):
        return None
    value = results.pop(chapter_id, None)
    if not results:
        db.info.pop(_DELETE_RESULTS, None)
    return value if isinstance(value, dict) else None


def _before_flush_chapter_delete(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    if session.info.get(_DELETE_GUARD):
        return
    deleted_projects = {
        row.id for row in session.deleted if isinstance(row, Project)
    }
    chapters = [
        row
        for row in session.deleted
        if isinstance(row, Chapter) and row.project_id not in deleted_projects
    ]
    if not chapters:
        return

    session.info[_DELETE_GUARD] = True
    try:
        grouped: dict[str, list[Chapter]] = defaultdict(list)
        for chapter in chapters:
            grouped[chapter.project_id].append(chapter)
        for project_id, deleted in grouped.items():
            order = {row.id: index for index, row in enumerate(_ordered_project_chapters(session, project_id))}
            trigger = min(deleted, key=lambda row: order.get(row.id, 1_000_000_000))
            result = rollback_cataloging_from_chapter(
                session,
                project_id,
                trigger.id,
                reason=f"《{trigger.title}》已删除；该章及后续章节的旧建档投影已回退",
                deleting_chapter=True,
            )
            results = session.info.setdefault(_DELETE_RESULTS, {})
            for chapter in deleted:
                results[chapter.id] = result
    finally:
        session.info.pop(_DELETE_GUARD, None)


def install_chapter_delete_rollback_listener() -> None:
    """Install the one process-wide chapter deletion rollback invariant."""

    global _LISTENER_INSTALLED
    if _LISTENER_INSTALLED:
        return
    event.listen(Session, "before_flush", _before_flush_chapter_delete, insert=True)
    _LISTENER_INSTALLED = True


install_chapter_delete_rollback_listener()


__all__ = [
    "chapter_suffix_ids",
    "install_chapter_delete_rollback_listener",
    "pop_delete_rollback_result",
    "rollback_cataloging_from_chapter",
]
