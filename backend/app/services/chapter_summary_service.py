"""Authoritative chapter-summary persistence shared by cataloging and author edits."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..database.models import Chapter, ChapterSummary


def upsert_chapter_summary_record(
    db: Session,
    chapter: Chapter,
    *,
    summary_text: str,
    key_events: list[str],
    source: str,
) -> tuple[ChapterSummary, dict[str, str | None] | None]:
    """Write the one authoritative summary row without changing chapter body version."""

    normalized_summary = str(summary_text or "").strip()
    if not normalized_summary:
        raise ValueError("章节摘要为空")
    normalized_events = [str(item).strip() for item in key_events if str(item).strip()]
    old = None
    summary = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter.id).first()
    if summary is None:
        summary = ChapterSummary(chapter_id=chapter.id, summary_text=normalized_summary)
        db.add(summary)
    else:
        old = {"summary_text": summary.summary_text, "key_events": summary.key_events}
        summary.summary_text = normalized_summary
    summary.key_events = json.dumps(normalized_events, ensure_ascii=False)
    summary.ai_model = str(source or "unknown")[:100]
    summary.updated_at = datetime.utcnow()
    db.flush()
    return summary, old


__all__ = ["upsert_chapter_summary_record"]
