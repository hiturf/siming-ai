"""Create cataloging candidates from streamed model lines."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, CatalogingChapterRun, CatalogingJob
from ..story_granularity import (
    CHARACTER_STABLE_FIELDS,
    CHARACTER_STATE_FIELDS,
    NARRATIVE_STATE_FIELDS,
    has_chapter_narrative_state,
)
from .candidate_io import float_or_none
from .candidate_validation import inspect_candidate_coverage
from .constants import VALID_ITEM_TYPES
from .jsonl import (
    candidate_response_attempts,
    clean_jsonl_text,
    expand_candidate_records,
    normalize_candidate,
    parse_candidate_response_records,
    parse_json_line,
)
from .repair_identity import has_stable_profile_evidence, is_anonymous_character
from ..character_role_types import normalize_character_role_type

_SIGNATURE_PAYLOAD_KEYS = (
    "dimension",
    "title",
    "name",
    "source_name",
    "target_name",
    "primary_name",
    "secondary_name",
    "relationship_type",
    "summary_text",
    "event",
    "event_description",
    "description",
    "content",
    "evidence",
)

_PLACEHOLDER_NAMES = {
    "未命名",
    "未命名角色",
    "未命名主角",
    "未命名设定",
    "未知",
    "无名",
    "角色名",
    "某人",
}

_CHARACTER_STATE_KEYS = set(CHARACTER_STATE_FIELDS)

_CHARACTER_DETAIL_KEYS = _CHARACTER_STATE_KEYS | (set(CHARACTER_STABLE_FIELDS) - {"name"})

_WORLDBUILDING_DETAIL_KEYS = {
    "content",
    "description",
    "event_description",
    "constraints",
    "plot_usage",
    "summary",
}


def _normalize_character_role_payload(normalized: dict[str, Any]) -> None:
    if normalized.get("item_type") not in {"character_create", "character_update"}:
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return
    if payload.get("role_type") not in (None, ""):
        raw_role = payload.get("role_type")
        payload["role_type"] = normalize_character_role_type(raw_role)


def _ensure_narrative_assessment_contract(
    normalized: dict[str, Any],
    *,
    source_task: str | None,
) -> None:
    """Make a missing assessment explicit without pretending the model ran it.

    Older/API-free agents only returned a summary and an outline.  Treating that
    omission as "no narrative issues" created a silent coverage hole, while
    rejecting the whole chapter made existing cataloging workflows unusable.
    Persist an empty state plus a fallback review instead: the archive can be
    applied, but the exact chapter revision remains visibly ``needs_review``.
    """

    if normalized.get("item_type") != "chapter_summary":
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return
    has_assessment = (
        isinstance(payload.get("narrative_state"), dict)
        or isinstance(payload.get("narrative_review"), dict)
        or isinstance(payload.get("governance_candidates"), list)
    )
    if has_assessment:
        return
    payload["narrative_state"] = {key: [] for key in NARRATIVE_STATE_FIELDS}
    payload["narrative_review"] = {
        "source": "fallback",
        "outcome": "assessment_missing",
        "requires_human_review": True,
        "evidence": (
            "The cataloging source did not provide a narrative-governance "
            "assessment; this chapter revision requires review."
        ),
        "source_task": source_task or "cataloging",
    }


def _ensure_outline_identity(
    normalized: dict[str, Any],
    run: CatalogingChapterRun,
) -> None:
    """Recover a missing chapter-outline title from the chapter being filed."""

    if normalized.get("item_type") not in {"outline_create", "outline_update"}:
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict) or _clean_value(payload.get("title")):
        return
    chapter = run.chapter
    if not chapter:
        return
    node_type = str(payload.get("node_type") or "chapter").strip().lower()
    if node_type == "chapter":
        payload["title"] = chapter.title
        normalized["target_name"] = normalized.get("target_name") or chapter.title
    elif node_type in {"section", "scene"} and payload.get("scene_number") is not None:
        title = f"{chapter.title} / 场景{payload['scene_number']}"
        payload["title"] = title
        payload.setdefault("parent_title", chapter.title)
        normalized["target_name"] = normalized.get("target_name") or title


def _signature_text(value: Any) -> str:
    if isinstance(value, list):
        text = " ".join(_signature_text(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "")
    return re.sub(r"\s+", "", text).strip().lower()


def _candidate_signature(
    *,
    item_type: str,
    target_name: str | None,
    payload: dict[str, Any],
    evidence: str | None,
) -> str:
    parts = [item_type]
    target = _signature_text(target_name)
    if target:
        parts.append(f"target:{target[:120]}")
    for key in _SIGNATURE_PAYLOAD_KEYS:
        value = _signature_text(payload.get(key))
        if value:
            parts.append(f"{key}:{value[:240]}")
    ev = _signature_text(evidence)
    if ev:
        parts.append(f"evidence:{ev[:240]}")
    if len(parts) == 1:
        parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[:800])
    return "|".join(parts)


def _clean_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean_value(item) for item in value).strip()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).strip()
    return str(value or "").strip()


def _has_any_text(payload: dict[str, Any], keys: set[str] | tuple[str, ...]) -> bool:
    return any(_clean_value(payload.get(key)) for key in keys)


def _is_placeholder_name(value: Any) -> bool:
    text = _clean_value(value)
    if not text:
        return True
    normalized = re.sub(r"[\s　:：;；,.，。]+", "", text)
    return normalized in _PLACEHOLDER_NAMES or normalized.startswith("未命名")


def _candidate_identity(normalized: dict[str, Any], *keys: str) -> str:
    payload = normalized.get("payload", {})
    for key in keys:
        value = normalized.get(key)
        if value:
            return _clean_value(value)
        if isinstance(payload, dict) and payload.get(key):
            return _clean_value(payload.get(key))
    return ""


def _skip_reason_for_candidate(normalized: dict[str, Any]) -> str | None:
    item_type = str(normalized.get("item_type") or "")
    payload = normalized.get("payload", {})
    if not isinstance(payload, dict):
        return "候选 payload 不是对象，已跳过"
    evidence = _clean_value(normalized.get("evidence") or payload.get("evidence"))

    if item_type in {"character_create", "character_update", "character_state_update", "character_timeline"}:
        identity = _candidate_identity(normalized, "id", "target_id", "target_name", "name", "character_name")
        if _is_placeholder_name(identity):
            return "角色候选缺少可识别姓名或ID，已跳过，避免生成未命名角色"
        if (
            item_type in {"character_create", "character_update"}
            and is_anonymous_character(identity)
            and not has_stable_profile_evidence(payload)
        ):
            return "身份未确认且缺少稳定档案，已保留为章节线索"
        if item_type == "character_state_update" and not _has_any_text(payload, _CHARACTER_STATE_KEYS):
            return f"角色状态候选 {identity} 没有状态字段，已跳过"
        if item_type in {"character_create", "character_update"} and not _has_any_text(
            payload, _CHARACTER_DETAIL_KEYS
        ):
            return f"角色候选 {identity} 只有姓名、没有可写入内容，已跳过"
        if item_type == "character_timeline" and not _clean_value(payload.get("event_description") or payload.get("event")):
            return f"角色时间线候选 {identity} 缺少事件描述，已跳过"

    if item_type == "character_relationship":
        source = _candidate_identity(normalized, "source_name", "source", "from_name", "character_a")
        target = _candidate_identity(normalized, "target_name", "target", "to_name", "character_b")
        if _is_placeholder_name(source) or _is_placeholder_name(target):
            return "关系候选缺少双方角色名，已跳过"
        if not (_clean_value(payload.get("relationship_type")) or _clean_value(payload.get("description")) or evidence):
            return f"关系候选 {source}-{target} 缺少关系内容，已跳过"

    if item_type in {"worldbuilding_create", "worldbuilding_update", "worldbuilding_timeline"}:
        title = _candidate_identity(normalized, "id", "target_id", "target_name", "title", "entry_title")
        if _is_placeholder_name(title):
            return "世界观候选缺少标题或ID，已跳过，避免生成未命名设定"
        if item_type == "worldbuilding_timeline":
            if not _clean_value(payload.get("event_description") or payload.get("event") or payload.get("description")):
                return f"世界观时间线候选 {title} 缺少事件描述，已跳过"
        elif not (_has_any_text(payload, _WORLDBUILDING_DETAIL_KEYS) or evidence):
            return f"世界观候选 {title} 没有内容，已跳过"

    if item_type == "chapter_summary":
        if not _clean_value(payload.get("summary_text") or payload.get("summary") or payload.get("content")) and not has_chapter_narrative_state(payload):
            return "章节摘要候选为空，已跳过"

    if item_type in {"outline_create", "outline_update"}:
        title = _candidate_identity(normalized, "target_name", "title", "chapter_title", "outline_title")
        if _is_placeholder_name(title):
            return "大纲候选缺少标题，已跳过"
        if not (_clean_value(payload.get("summary")) or _clean_value(payload.get("description")) or _clean_value(payload.get("purpose"))):
            return f"大纲候选 {title} 缺少摘要/作用，已跳过"

    return None


def _payload_from_candidate(candidate: CatalogingCandidate) -> dict[str, Any]:
    try:
        parsed = json.loads(candidate.edited_payload or candidate.raw_payload or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _matching_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    normalized: dict[str, Any],
) -> CatalogingCandidate | None:
    item_type = normalized["item_type"]
    signature = _candidate_signature(
        item_type=item_type,
        target_name=str(normalized.get("target_name") or "") or None,
        payload=normalized["payload"],
        evidence=str(normalized.get("evidence") or "") or None,
    )
    # Candidates are review artifacts owned by one cataloging run.  A card
    # produced by an older run must not suppress the same card in a retry or a
    # later re-cataloging job; entity-level upsert/deduplication happens in the
    # applier.  Keeping this scope run-local also lets completeness validation
    # see every required card in the current run.
    query = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.item_type == item_type,
        CatalogingCandidate.status != "rejected",
    )
    for existing in query.all():
        existing_signature = _candidate_signature(
            item_type=existing.item_type,
            target_name=existing.target_name,
            payload=_payload_from_candidate(existing),
            evidence=existing.evidence,
        )
        if existing_signature == signature:
            return existing
    # A chapter has exactly one summary card.  A later call often repairs a
    # partial first attempt by adding the coverage manifest or governance
    # assessment while keeping the same summary text.  Treat that as an
    # idempotent upgrade instead of making the incomplete card impossible to
    # correct.
    if item_type == "chapter_summary":
        return query.order_by(CatalogingCandidate.sort_order.asc()).first()
    # One cataloging run owns exactly one chapter-level outline. Incremental
    # model repairs upgrade that staged card instead of creating duplicates.
    if item_type in {"outline_create", "outline_update"}:
        node_type = str(normalized["payload"].get("node_type") or "chapter").lower()
        if node_type == "chapter":
            outline_query = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type.in_(("outline_create", "outline_update")),
                CatalogingCandidate.status != "rejected",
            )
            for existing in outline_query.order_by(CatalogingCandidate.sort_order.asc()).all():
                existing_payload = _payload_from_candidate(existing)
                if str(existing_payload.get("node_type") or "chapter").lower() == "chapter":
                    return existing
    return None


def _merge_unique_values(existing: list[Any], incoming: list[Any]) -> list[Any]:
    merged = list(existing)
    signatures = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        for item in merged
    }
    for item in incoming:
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if signature not in signatures:
            merged.append(item)
            signatures.add(signature)
    return merged


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _merge_coverage_manifest(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Only add to an accepted manifest; a retry cannot shrink its contract."""

    merged = dict(existing)
    for key, value in incoming.items():
        old_value = merged.get(key)
        if key == "scene_count":
            merged[key] = max(_positive_int(old_value), _positive_int(value)) or value
        elif isinstance(old_value, list) and isinstance(value, list):
            merged[key] = _merge_unique_values(old_value, value)
        elif key not in merged or old_value in (None, "", [], {}):
            merged[key] = value
    return merged


def _merge_candidate_payload(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    item_type: str = "",
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_candidate_payload(merged[key], value)
            continue
        # Explicit empty arrays/objects still matter when the old payload did
        # not declare the field.  Do not let a later empty value erase richer
        # data that was already staged.
        if key not in merged or value not in (None, "", [], {}):
            merged[key] = value
    if item_type == "chapter_summary":
        old_manifest = existing.get("coverage_manifest")
        new_manifest = incoming.get("coverage_manifest")
        if isinstance(old_manifest, dict) and isinstance(new_manifest, dict):
            merged["coverage_manifest"] = _merge_coverage_manifest(
                old_manifest,
                new_manifest,
            )
        old_scene_count = _positive_int(existing.get("scene_count"))
        new_scene_count = _positive_int(incoming.get("scene_count"))
        if old_scene_count or new_scene_count:
            merged["scene_count"] = max(old_scene_count, new_scene_count)
    return merged


def try_create_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    line: str,
    sort_order: int,
) -> dict[str, Any]:
    results = try_create_candidates(db, job, run, line, sort_order)
    if not results:
        return {}
    if len(results) == 1:
        return results[0]
    candidates = [result["candidate"] for result in results if result.get("candidate")]
    combined: dict[str, Any] = {"results": results, "candidates": candidates}
    if candidates:
        combined["candidate"] = candidates[0]
    skipped = [result.get("reason") for result in results if result.get("skipped")]
    if skipped:
        combined["skipped_reasons"] = skipped
    errors = [result for result in results if result.get("bad_line")]
    if errors:
        combined["errors"] = errors
    return combined


def try_create_candidates(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    line: str,
    sort_order: int,
) -> list[dict[str, Any]]:
    text = clean_jsonl_text(line)
    if not text:
        return []
    last_sort_order = (
        db.query(CatalogingCandidate.sort_order)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .order_by(CatalogingCandidate.sort_order.desc())
        .first()
    )
    next_sort_order = (
        int(last_sort_order[0] or 0) + 1
        if last_sort_order is not None
        else 0
    )
    base_sort_order = max(int(sort_order or 0), next_sort_order)
    try:
        parsed = parse_json_line(text)
        if parsed is None:
            return []
        return [
            create_candidate_from_raw(
                db,
                job,
                run,
                record,
                base_sort_order + offset,
            )
            for offset, record in enumerate(expand_candidate_records(parsed))
        ]
    except Exception as exc:
        return [{"bad_line": text, "error": str(exc)}]


def _preview_candidate_from_raw(
    run: CatalogingChapterRun,
    raw: dict[str, Any],
    *,
    source_task: str,
) -> dict[str, Any] | None:
    """Normalize one record for coverage checks without writing it."""

    normalized = normalize_candidate(raw)
    _normalize_character_role_payload(normalized)
    _ensure_narrative_assessment_contract(
        normalized,
        source_task=source_task or normalized.get("source_task"),
    )
    _ensure_outline_identity(normalized, run)
    if normalized["item_type"] not in VALID_ITEM_TYPES:
        return None
    if _skip_reason_for_candidate(normalized):
        return None
    return {
        "item_type": normalized["item_type"],
        "status": "pending",
        "payload": normalized["payload"],
    }


def _existing_recovery_candidates(
    db: Session,
    run: CatalogingChapterRun,
) -> list[CatalogingCandidate]:
    return (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.status != "rejected",
        )
        .all()
    )


def _preview_response_records(
    run: CatalogingChapterRun,
    records: list[dict[str, Any]],
    *,
    source_task: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid_records: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []
    for record in records:
        normalized = _preview_candidate_from_raw(
            run,
            record,
            source_task=source_task,
        )
        if normalized is None:
            continue
        valid_records.append(record)
        preview.append(normalized)
    return valid_records, preview


def recover_candidates_from_response_text(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    text: str,
    *,
    source_task: str = "response_recovery",
) -> dict[str, Any]:
    """Recover a complete candidate set from a provider's whole response.

    Streaming JSONL remains the fast path.  This boundary adapter is invoked
    before a retry and accepts common provider deviations such as a JSON array,
    pretty-printed JSON, fenced JSON, a collection wrapper, or a typed summary
    object containing the other candidate arrays.  Nothing is persisted unless
    the recovered records plus valid cards already in this run pass the same
    completeness gate as normal cataloging.
    """

    records = parse_candidate_response_records(text)
    valid_records, preview = _preview_response_records(
        run,
        records,
        source_task=source_task,
    )
    existing = _existing_recovery_candidates(db, run)
    proposed = [*existing, *preview]
    coverage = inspect_candidate_coverage(
        proposed,
        db=db,
        project_id=job.project_id,
    )
    if not valid_records or not coverage.is_complete:
        return {
            "results": [],
            "coverage": coverage,
            "record_count": len(records),
        }

    sort_order = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
    ).count()
    results = [
        create_candidate_from_raw(
            db,
            job,
            run,
            record,
            sort_order + offset,
            source_task=source_task,
        )
        for offset, record in enumerate(valid_records)
    ]
    final_coverage = inspect_candidate_coverage(
        _existing_recovery_candidates(db, run),
        db=db,
        project_id=job.project_id,
    )
    return {
        "results": results,
        "coverage": final_coverage,
        "record_count": len(records),
    }


def recover_candidates_from_raw_output(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
) -> dict[str, Any]:
    """Recover the newest complete attempt stored on a failed chapter run."""

    attempts = candidate_response_attempts(run.raw_output or "")
    existing = _existing_recovery_candidates(db, run)
    combined_fallback: tuple[int, str] | None = None
    last_coverage = inspect_candidate_coverage(existing, db=db, project_id=job.project_id)

    # Prefer a self-contained attempt so records from different retries are not
    # mixed.  If no attempt is independently complete, allow the newest attempt
    # to complement cards already parsed from that same run.
    for reverse_index, attempt_text in enumerate(reversed(attempts), start=1):
        records = parse_candidate_response_records(attempt_text)
        _, preview = _preview_response_records(
            run,
            records,
            source_task="raw_output_recovery",
        )
        attempt_items = list(preview)
        combined_items = [*existing, *preview]
        attempt_coverage = inspect_candidate_coverage(
            attempt_items,
            db=db,
            project_id=job.project_id,
        )
        combined_coverage = inspect_candidate_coverage(
            combined_items,
            db=db,
            project_id=job.project_id,
        )
        last_coverage = combined_coverage
        if attempt_coverage.is_complete:
            recovered = recover_candidates_from_response_text(
                db,
                job,
                run,
                attempt_text,
                source_task="raw_output_recovery",
            )
            recovered["attempt_from_end"] = reverse_index
            return recovered
        if combined_fallback is None and combined_coverage.is_complete:
            combined_fallback = (reverse_index, attempt_text)

    if combined_fallback is not None:
        reverse_index, attempt_text = combined_fallback
        recovered = recover_candidates_from_response_text(
            db,
            job,
            run,
            attempt_text,
            source_task="raw_output_recovery",
        )
        recovered["attempt_from_end"] = reverse_index
        return recovered

    return {
        "results": [],
        "coverage": last_coverage,
        "record_count": 0,
        "attempt_from_end": None,
    }


def create_candidate_from_raw(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    raw: dict[str, Any],
    sort_order: int,
    *,
    source_task: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_candidate(raw)
    _normalize_character_role_payload(normalized)
    _ensure_narrative_assessment_contract(
        normalized,
        source_task=source_task or normalized.get("source_task"),
    )
    _ensure_outline_identity(normalized, run)
    if normalized["item_type"] not in VALID_ITEM_TYPES:
        return {
            "bad_line": json.dumps(raw, ensure_ascii=False),
            "error": _unknown_type_message(raw, normalized),
        }
    skip_reason = _skip_reason_for_candidate(normalized)
    if skip_reason:
        return {"skipped": True, "reason": skip_reason}
    matching = _matching_candidate(db, job, run, normalized)
    if matching:
        old_payload = _payload_from_candidate(matching)
        merged_payload = _merge_candidate_payload(
            old_payload,
            normalized["payload"],
            item_type=normalized["item_type"],
        )
        if merged_payload == old_payload:
            return {"duplicate": True}
        matching.raw_payload = json.dumps(merged_payload, ensure_ascii=False)
        matching.edited_payload = None
        matching.operation = normalized["operation"] or matching.operation
        matching.target_type = normalized.get("target_type") or matching.target_type
        matching.target_id = normalized.get("target_id") or matching.target_id
        matching.target_name = (
            str(normalized.get("target_name") or "")[:200]
            or matching.target_name
        )
        matching.confidence = float_or_none(normalized.get("confidence")) or matching.confidence
        matching.evidence = (
            str(normalized.get("evidence") or "")[:2000]
            or matching.evidence
        )
        matching.source_task = source_task or normalized.get("source_task") or matching.source_task
        db.flush()
        return {"candidate": matching, "updated": True}
    candidate = CatalogingCandidate(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=job.project_id,
        chapter_id=run.chapter_id,
        item_type=normalized["item_type"],
        operation=normalized["operation"],
        target_type=normalized.get("target_type"),
        target_id=normalized.get("target_id"),
        target_name=str(normalized.get("target_name") or "")[:200] or None,
        raw_payload=json.dumps(normalized["payload"], ensure_ascii=False),
        status="pending",
        confidence=float_or_none(normalized.get("confidence")),
        evidence=str(normalized.get("evidence") or "")[:2000] or None,
        sort_order=sort_order,
        source_task=source_task or normalized.get("source_task"),
    )
    db.add(candidate)
    db.flush()
    return {"candidate": candidate}


def _unknown_type_message(raw: dict[str, Any], normalized: dict[str, Any]) -> str:
    raw_type = (
        raw.get("type")
        or raw.get("item_type")
        or raw.get("candidate_type")
        or raw.get("kind")
        or raw.get("card_type")
        or ""
    )
    payload_keys = ", ".join(sorted(str(key) for key in normalized.get("payload", {}).keys())[:12])
    raw_keys = ", ".join(sorted(str(key) for key in raw.keys())[:12])
    snippet = json.dumps(raw, ensure_ascii=False, default=str)[:240]
    if raw_type:
        return f"未知 type: {raw_type}（raw_fields: {raw_keys or 'none'}, payload_fields: {payload_keys or 'none'}）"
    return (
        "未知 type: <empty>，无法从字段推断候选类型"
        f"（raw_fields: {raw_keys or 'none'}, payload_fields: {payload_keys or 'none'}, snippet: {snippet}）"
    )
