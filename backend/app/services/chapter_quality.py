"""Non-destructive, user-triggered chapter quality scoring."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..core.exceptions import LLMError, NotFoundError, ValidationError
from ..core.json_repair import parse_json_object
from ..core.utils import count_words
from ..database.models import Chapter, Project
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..prompts.chapter_evaluation_prompts import build_chapter_evaluation_messages
from .narrative_governance import record_quality_metric

QUALITY_DIMENSIONS = (
    "开头吸引力",
    "情节推进",
    "角色塑造",
    "对话质量",
    "悬念设置",
    "节奏控制",
    "展示性描写",
    "语言质量",
)


def _bounded_int(value: Any, *, minimum: int = 0, maximum: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = minimum
    return min(maximum, max(minimum, number))


def _normalize_quality_report(raw_report: dict[str, Any]) -> dict[str, Any]:
    raw_scores = raw_report.get("scores")
    if not isinstance(raw_scores, list) or len(raw_scores) < len(QUALITY_DIMENSIONS):
        raise LLMError("质量评分结果缺少完整的 8 个维度，请重新评分")

    by_name = {
        str(item.get("dimension") or "").strip(): item
        for item in raw_scores
        if isinstance(item, dict)
    }
    normalized_scores: list[dict[str, Any]] = []
    for index, dimension in enumerate(QUALITY_DIMENSIONS):
        fallback = raw_scores[index] if isinstance(raw_scores[index], dict) else {}
        item = by_name.get(dimension, fallback)
        normalized_scores.append(
            {
                "dimension": dimension,
                "score": _bounded_int(item.get("score"), maximum=10),
                "comment": str(item.get("comment") or "暂无具体评价").strip(),
            }
        )

    improvements = raw_report.get("bottom3_improvements")
    if not isinstance(improvements, list):
        improvements = []

    return {
        # Recalculate the total so a malformed model total cannot disagree with
        # the eight visible dimension scores.
        "total_score": sum(item["score"] for item in normalized_scores),
        "max_score": 80,
        "scores": normalized_scores,
        "ai_flavor_count": _bounded_int(
            raw_report.get("ai_flavor_count"), maximum=100_000
        ),
        "overall_assessment": str(
            raw_report.get("overall_assessment") or "暂无总体评价"
        ).strip(),
        "bottom3_improvements": [
            str(item).strip() for item in improvements[:3] if str(item).strip()
        ],
    }


async def preview_chapter_quality(
    db: Session,
    project_id: str,
    chapter_id: str | None,
    *,
    content: str,
    title: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Score the current editor text without writing chapter content or metadata."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    chapter = None
    if chapter_id:
        chapter = (
            db.query(Chapter)
            .filter(Chapter.id == chapter_id, Chapter.project_id == project_id)
            .first()
        )
        if not chapter:
            raise NotFoundError("章节不存在")

    source = str(content or "")
    if len(source.strip()) < 20:
        raise ValidationError("正文太短，至少需要 20 个字符才能进行质量评分")

    messages = build_chapter_evaluation_messages(
        chapter_title=str(title or (chapter.title if chapter else "") or "未命名章节").strip(),
        chapter_content=source,
    )
    extra_body = LLMGateway.local_cli_extra_body(
        model,
        base={
            "moshu_task_type": "evaluation",
            "moshu_project_id": project_id,
            # The editor passes the text explicitly. Scoring needs neither the
            # filesystem nor MCP and therefore must stay isolated for CLI models.
            "local_cli_isolated": True,
        },
    )
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.2,
            max_tokens=3_000,
            timeout=180,
            retry=1,
            extra_body=extra_body,
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"质量评分失败：{exc}") from exc

    parsed = parse_json_object(str(result.get("content") or ""))
    if not parsed:
        raise LLMError("质量评分结果无法解析，请重新评分")
    report = _normalize_quality_report(parsed)
    request_meta = (
        result.get("request_meta")
        if isinstance(result.get("request_meta"), dict)
        else {}
    )
    score_by_dimension = {
        item["dimension"]: item["score"] * 10
        for item in report["scores"]
    }
    metric = None
    if chapter is not None:
        metric = record_quality_metric(
            db,
            project_id,
            {
                "chapter_id": chapter.id,
                "chapter_version": chapter.current_version or 1,
                "plot_tension": score_by_dimension.get("悬念设置"),
                "emotional_tension": score_by_dimension.get("角色塑造"),
                "pacing_density": score_by_dimension.get("节奏控制"),
                "character_consistency": score_by_dimension.get("角色塑造"),
                "viewpoint_consistency": score_by_dimension.get("展示性描写"),
                "world_consistency": score_by_dimension.get("情节推进"),
                "passed": report["total_score"] >= 48,
                "warnings": report["bottom3_improvements"],
                "evidence": report["overall_assessment"],
                "total_score": report["total_score"],
                "max_score": report["max_score"],
                "dimension_scores": report["scores"],
                "overall_assessment": report["overall_assessment"],
                "model": str(request_meta.get("model") or result.get("model") or model or ""),
                "source": "manual_quality_button",
            },
        )
    return {
        "chapter_id": chapter.id if chapter else None,
        "word_count": count_words(source),
        "provider": str(request_meta.get("provider") or ""),
        "model": str(request_meta.get("model") or result.get("model") or model or ""),
        "mutated": False,
        "recorded": metric is not None,
        "quality_metric_id": metric.id if metric else None,
        **report,
    }
