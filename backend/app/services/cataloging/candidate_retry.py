"""Incremental cataloging candidate retry and coverage diagnostics."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, CatalogingChapterRun
from ..story_granularity import normalize_node_type, normalize_section_scene_state
from .candidate_validation import (
    candidate_coverage_error_message,
    candidate_coverage_review_message,
    candidate_coverage_should_retry,
    inspect_candidate_coverage,
)


def append_incremental_candidate_retry(prompt: str, reason: str) -> str:
    return prompt + (
        "\n\n【上一轮校验未通过，执行增量修复】\n"
        f"{reason}\n"
        "本节规则优先于上面的首次生成要求。系统已保留上一轮成功入库的候选；"
        "只输出错误信息明确指出的缺失候选，以及上一轮解析失败、身份不一致或结构错误候选的修正版。\n"
        "chapter_summary 和 chapter_outline 仅在错误信息明确指出缺失时输出；"
        "不要重复任何已通过候选，也不要重发完整候选集。\n"
        "不得降低或重写既有 coverage_manifest.scene_count；错误列出缺失 scene_number 时，"
        "逐个输出对应的 section outline_create，并补齐全部场景状态字段。\n"
        "每行只输出一个标准候选 JSON 对象；不要输出聚合总对象、Markdown、解释或代码块。"
    )


def candidate_coverage_for_run(db: Session, run: CatalogingChapterRun):
    candidates = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .filter(CatalogingCandidate.status != "rejected")
        .all()
    )
    return inspect_candidate_coverage(candidates, db=db, project_id=run.project_id)


def _cataloging_candidate_payload(candidate: CatalogingCandidate) -> dict[str, Any]:
    try:
        value = json.loads(candidate.edited_payload or candidate.raw_payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _scene_repair_details(
    candidates: list[CatalogingCandidate],
    scene_count: int,
) -> list[str]:
    if scene_count <= 1:
        return []
    section_numbers: set[int] = set()
    state_numbers: set[int] = set()
    for candidate in candidates:
        if candidate.item_type not in {"outline_create", "outline_update"}:
            continue
        payload = _cataloging_candidate_payload(candidate)
        if normalize_node_type(payload.get("node_type")) != "section":
            continue
        try:
            scene_number = int(payload.get("scene_number"))
        except (TypeError, ValueError):
            continue
        if scene_number <= 0:
            continue
        section_numbers.add(scene_number)
        if normalize_section_scene_state(payload):
            state_numbers.add(scene_number)
    expected = set(range(1, scene_count + 1))
    details: list[str] = []
    missing_sections = sorted(expected - section_numbers)
    if missing_sections:
        details.append("缺少 section 场景编号：" + "、".join(map(str, missing_sections)))
    missing_states = sorted(expected - state_numbers)
    if missing_states:
        details.append(
            "缺少场景状态字段的 scene_number：" + "、".join(map(str, missing_states))
        )
    return details


def candidate_coverage_error(db: Session, run: CatalogingChapterRun) -> str:
    candidates = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .filter(CatalogingCandidate.status != "rejected")
        .all()
    )
    coverage = inspect_candidate_coverage(candidates, db=db, project_id=run.project_id)
    if coverage.is_complete:
        return ""
    message = candidate_coverage_error_message(coverage)
    details = _scene_repair_details(candidates, coverage.scene_count)
    return message if not details else message + "；" + "；".join(details)


def candidate_coverage_requires_model_retry(
    db: Session,
    run: CatalogingChapterRun,
) -> bool:
    return candidate_coverage_should_retry(candidate_coverage_for_run(db, run))


def candidate_coverage_review(db: Session, run: CatalogingChapterRun) -> str:
    return candidate_coverage_review_message(candidate_coverage_for_run(db, run))
