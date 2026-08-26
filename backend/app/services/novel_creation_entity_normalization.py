"""Deterministic normalization for generated creation entities."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .character_role_types import normalize_character_role_type
from .novel_creation_authoring import _dedupe_dicts, _dict_rows


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_characters(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    source_rows = _dict_rows(data.get("characters"))
    base_rows = _dict_rows(baseline.get("characters"))
    if not source_rows:
        source_rows = deepcopy(base_rows)
    base_by_name = {_text(row.get("name")): row for row in base_rows if _text(row.get("name"))}
    characters: list[dict[str, Any]] = []
    for index, source in enumerate(source_rows):
        name = _text(source.get("name"))
        base = base_by_name.get(name) or (base_rows[index] if index < len(base_rows) else {})
        item = {**deepcopy(base), **deepcopy(source)}
        item["name"] = name or _text(base.get("name")) or f"角色{index + 1}"
        profile = {
            **deepcopy(base.get("profile") if isinstance(base.get("profile"), dict) else {}),
            **deepcopy(item.get("profile") if isinstance(item.get("profile"), dict) else {}),
        }
        source_profile = source.get("profile") if isinstance(source.get("profile"), dict) else {}
        role_type = normalize_character_role_type(
            source.get("role_type") or base.get("role_type"),
            default=None,
        )
        goal = _text(
            source.get("goal")
            or source.get("current_goal")
            or source_profile.get("core_motivation")
            or base.get("goal")
            or profile.get("core_motivation")
        )
        item.update({
            "role_type": role_type,
            "goal": goal,
            "current_goal": goal,
            "background": _text(item.get("background") or item.get("position") or item.get("status")) or None,
        })
        if not _text(profile.get("core_motivation")):
            profile["core_motivation"] = goal
        item["profile"] = profile
        characters.append(item)
    characters = _dedupe_dicts(characters, lambda item: _text(item.get("name")).casefold())
    relationships = _dict_rows(data.get("relationships"), name_field="id") or _dict_rows(
        baseline.get("relationships"),
        name_field="id",
    )
    relationships = _dedupe_dicts(
        relationships,
        lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
    )
    return {**deepcopy(baseline), **deepcopy(data), "characters": characters, "relationships": relationships}


def normalize_locations(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    entries = _dict_rows(data.get("entries"), name_field="title") + _dict_rows(
        baseline.get("entries"),
        name_field="title",
    )
    entries = _dedupe_dicts(entries, lambda item: _text(item.get("title")).casefold())
    relations = _dict_rows(data.get("relations"), name_field="id") + _dict_rows(
        baseline.get("relations"),
        name_field="id",
    )
    relations = _dedupe_dicts(
        relations,
        lambda item: (
            _text(item.get("source_title")).casefold(),
            _text(item.get("target_title")).casefold(),
            _text(item.get("relation_type")).casefold(),
        ),
    )
    return {**deepcopy(baseline), **deepcopy(data), "entries": entries, "relations": relations}
