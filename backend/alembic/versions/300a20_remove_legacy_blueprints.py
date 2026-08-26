"""Remove the retired full-blueprint persistence path.

Revision ID: 300a20_remove_blueprints
Revises: 300a18_user_chapter_cataloging
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "300a20_remove_blueprints"
down_revision = "300a18_user_chapter_cataloging"
branch_labels = None
depends_on = None


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if isinstance(value, dict):
        value = [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any, default: str = "") -> str:
    result = str(value or "").strip()
    return result or default


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _concept(legacy: dict[str, Any], index: int) -> dict[str, Any]:
    protagonist = _object(legacy.get("protagonist"))
    creative = _object(legacy.get("creative_slots"))
    golden = _object(legacy.get("golden_three"))
    coverage = _object(legacy.get("requirement_coverage"))
    concept_id = f"concept-{index + 1}"
    return {
        "id": concept_id,
        "source_index": index,
        "title": _text(legacy.get("title"), f"创意方向 {index + 1}"),
        "subtitle": _text(legacy.get("subtitle") or legacy.get("genre_positioning")),
        "logline": _text(legacy.get("logline") or legacy.get("premise")),
        "protagonist_seed": {
            "name": _text(protagonist.get("name"), "待命名主角"),
            "identity": _text(protagonist.get("background")),
            "goal": _text(protagonist.get("goal")),
            "lack": _text(protagonist.get("weakness") or protagonist.get("conflict")),
        },
        "world_hook": _text(
            creative.get("world_rules") or legacy.get("world_hook") or legacy.get("premise")
        ),
        "core_conflict": _text(legacy.get("core_conflict") or protagonist.get("conflict")),
        "story_engine": _text(creative.get("story_engine") or legacy.get("story_engine")),
        "opening_hook": _text(golden.get("opening_scene") or golden.get("chapter_1")),
        "differentiators": _list(legacy.get("selling_points"))[:3],
        "risks": _list(legacy.get("risks"))[:2],
        "coverage": {
            "score": int(coverage.get("score") or 0),
            "covered": _list(coverage.get("covered")),
            "missing": _list(coverage.get("missing")),
        },
    }


def _migrate_creation_seeds(bind) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, blueprint_json, draft_json FROM novel_creation_sessions "
            "WHERE blueprint_json IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        concepts = [_concept(item, index) for index, item in enumerate(_items(row["blueprint_json"]))]
        if not concepts:
            continue
        draft = deepcopy(_object(row["draft_json"]))
        if not isinstance(draft.get("concepts"), list) or not draft["concepts"]:
            draft["concepts"] = concepts
        if not isinstance(draft.get("concept_seeds"), dict) or not draft["concept_seeds"]:
            draft["concept_seeds"] = {item["id"]: deepcopy(item) for item in concepts}
        if not draft.get("selected_concept_id"):
            draft["selected_concept_id"] = concepts[0]["id"]
        stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
        if not isinstance(stages.get("concepts"), dict):
            now = datetime.now(timezone.utc).isoformat()
            stages["concepts"] = {
                "status": "generated",
                "data": {
                    "options": deepcopy(draft["concepts"]),
                    "selected_concept_id": draft["selected_concept_id"],
                },
                "source": "migration",
                "updated_at": now,
            }
        draft["stages"] = stages
        draft["schema_version"] = max(3, int(draft.get("schema_version") or 0))
        bind.execute(
            sa.text("UPDATE novel_creation_sessions SET draft_json = :draft WHERE id = :id"),
            {"id": row["id"], "draft": json.dumps(draft, ensure_ascii=False)},
        )


def _drop_column_if_present(table: str, column: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    if column not in {item["name"] for item in inspector.get_columns(table)}:
        return
    op.drop_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "novel_creation_sessions" in tables:
        columns = {item["name"] for item in inspector.get_columns("novel_creation_sessions")}
        if "blueprint_json" in columns:
            _migrate_creation_seeds(bind)
    _drop_column_if_present("novel_creation_sessions", "blueprint_json")
    _drop_column_if_present("system_assistant_conversations", "blueprint_json")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "novel_creation_sessions" in tables:
        columns = {item["name"] for item in inspector.get_columns("novel_creation_sessions")}
        if "blueprint_json" not in columns:
            op.add_column("novel_creation_sessions", sa.Column("blueprint_json", sa.JSON(), nullable=True))
    if "system_assistant_conversations" in tables:
        columns = {item["name"] for item in inspector.get_columns("system_assistant_conversations")}
        if "blueprint_json" not in columns:
            op.add_column(
                "system_assistant_conversations",
                sa.Column("blueprint_json", sa.JSON(), nullable=True),
            )
