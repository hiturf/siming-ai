"""Per-user-turn category state shared with temporary MCP processes."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
)
from app.modules.creation.interfaces.agent_scope import (
    CREATION_AGENT_WRITE_TOOL_NAMES,
    CREATION_TURN_MAX_FAILED_WRITES,
    CREATION_TURN_MAX_SUCCESSFUL_WRITES,
    CREATION_WRITE_SUCCESS_STATUSES,
    creation_turn_write_denial,
    creation_turn_writes_closed,
)


TOOL_CATEGORY_STATE_SCHEMA = "tool_categories.v2"
_STATE_DIR_PREFIX = "siming-tool-categories-"


def create_tool_category_state() -> str:
    root = Path(tempfile.mkdtemp(prefix=_STATE_DIR_PREFIX))
    path = root / "state.json"
    _write_state(path, {
        "schema": TOOL_CATEGORY_STATE_SCHEMA,
        "version": 0,
        "active_version": 0,
        "active_categories": [],
        "requested_categories": [],
        "creation_turn": {
            "successful_writes": 0,
            "failed_writes": 0,
            "write_limit": CREATION_TURN_MAX_SUCCESSFUL_WRITES,
            "failed_write_limit": CREATION_TURN_MAX_FAILED_WRITES,
            "last_write_tool": "",
            "last_write_status": "",
        },
    })
    (root / "events.ndjson").touch()
    (root / "audit.ndjson").touch()
    return str(path)


def _validated_path(path: str) -> Path | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).resolve()
    if candidate.name != "state.json" or not candidate.parent.name.startswith(_STATE_DIR_PREFIX):
        return None
    if candidate.parent.parent != Path(tempfile.gettempdir()).resolve():
        return None
    return candidate


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def read_tool_category_state(path: str) -> dict[str, Any]:
    state_path = _validated_path(path)
    if state_path is None or not state_path.exists():
        raise ValueError("工具类别状态文件无效")
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("工具类别状态文件不可读") from exc
    if not isinstance(value, dict) or value.get("schema") != TOOL_CATEGORY_STATE_SCHEMA:
        raise ValueError("工具类别状态文件契约不匹配")
    value["active_categories"] = list(normalize_tool_categories(value.get("active_categories") or []))
    value["requested_categories"] = list(normalize_tool_categories(
        value.get("requested_categories", value.get("active_categories")) or [],
    ))
    value["version"] = int(value.get("version") or 0)
    value["active_version"] = int(value.get("active_version") or 0)
    raw_turn = value.get("creation_turn")
    turn = dict(raw_turn) if isinstance(raw_turn, dict) else {}
    turn["successful_writes"] = max(0, int(turn.get("successful_writes") or 0))
    turn["failed_writes"] = max(0, int(turn.get("failed_writes") or 0))
    turn["write_limit"] = CREATION_TURN_MAX_SUCCESSFUL_WRITES
    turn["failed_write_limit"] = CREATION_TURN_MAX_FAILED_WRITES
    turn["last_write_tool"] = str(turn.get("last_write_tool") or "")
    turn["last_write_status"] = str(turn.get("last_write_status") or "")
    value["creation_turn"] = turn
    return value


def creation_turn_write_denial_for_state(
    path: str,
    tool_name: str,
) -> dict[str, Any] | None:
    """Reject a creation mutation after this user turn's budget is closed."""

    state = read_tool_category_state(path)
    turn = state["creation_turn"]
    return creation_turn_write_denial(
        tool_name,
        successful_writes=int(turn["successful_writes"]),
        failed_writes=int(turn["failed_writes"]),
    )


def creation_turn_write_tools_closed(state: dict[str, Any]) -> bool:
    turn = state.get("creation_turn") if isinstance(state.get("creation_turn"), dict) else {}
    return creation_turn_writes_closed(
        successful_writes=max(0, int(turn.get("successful_writes") or 0)),
        failed_writes=max(0, int(turn.get("failed_writes") or 0)),
    )


def record_creation_turn_write_result(
    path: str,
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist one executed creation-write outcome and return a stop event."""

    if tool_name not in CREATION_AGENT_WRITE_TOOL_NAMES:
        return None
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    turn = state["creation_turn"]
    status = str(result.get("status") or "error")
    if status in CREATION_WRITE_SUCCESS_STATUSES:
        turn["successful_writes"] = int(turn["successful_writes"]) + 1
    else:
        turn["failed_writes"] = int(turn["failed_writes"]) + 1
    turn["last_write_tool"] = tool_name
    turn["last_write_status"] = status
    state["creation_turn"] = turn
    _write_state(state_path, state)

    failed_writes = int(turn["failed_writes"])
    if status not in CREATION_WRITE_SUCCESS_STATUSES and failed_writes >= CREATION_TURN_MAX_FAILED_WRITES:
        return {
            "type": "tool_completed",
            "message": "写入连续失败已达上限，本轮已停止自动重试",
            "data": {
                "tool": tool_name,
                "status": "denied",
                "turn_boundary": "failed_write_limit",
                "failed_writes": failed_writes,
            },
        }
    return None


def remove_tool_category_state(path: str) -> None:
    state_path = _validated_path(path)
    if state_path is None:
        return
    try:
        read_tool_category_state(path)
    except ValueError:
        return
    shutil.rmtree(state_path.parent, ignore_errors=True)


def replace_tool_categories(path: str, value: Any) -> dict[str, Any]:
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    categories = normalize_tool_categories(value)
    state["version"] = int(state.get("version") or 0) + 1
    state["requested_categories"] = list(categories)
    _write_state(state_path, state)
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
    detail = f"已准备{'、'.join(labels)}能力" if labels else "已关闭全部业务工具"
    event = {
        "type": "tool_categories_changed",
        "message": detail,
        "data": {"enabled_categories": list(categories), "labels": labels},
    }
    append_tool_category_event(path, event)
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": event["data"],
    }


def activate_tool_categories(path: str) -> dict[str, Any]:
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    state["active_categories"] = list(normalize_tool_categories(
        state.get("requested_categories") or [],
    ))
    state["active_version"] = int(state.get("version") or 0)
    _write_state(state_path, state)
    return state


def append_tool_category_event(path: str, event: dict[str, Any]) -> None:
    state_path = _validated_path(path)
    if state_path is None:
        return
    try:
        read_tool_category_state(path)
    except ValueError:
        return
    with (state_path.parent / "events.ndjson").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        handle.flush()


def append_tool_category_audit(path: str, record: dict[str, Any]) -> None:
    state_path = _validated_path(path)
    if state_path is None:
        return
    try:
        read_tool_category_state(path)
    except ValueError:
        return
    encoded = json.dumps(record, ensure_ascii=False, default=str)
    if len(encoded) > 500_000:
        encoded = json.dumps({
            "tool": record.get("tool"),
            "status": record.get("status"),
            "truncated": True,
            "encoded_preview": encoded[:500_000],
        }, ensure_ascii=False)
    with (state_path.parent / "audit.ndjson").open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()


def _read_ndjson(path: str, name: str) -> list[dict[str, Any]]:
    state_path = _validated_path(path)
    if state_path is None:
        return []
    try:
        rows = (state_path.parent / name).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    values: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def read_tool_category_events(path: str, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    state_path = _validated_path(path)
    if state_path is None:
        return [], offset
    events_path = state_path.parent / "events.ndjson"
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            handle.seek(max(offset, 0))
            rows = handle.readlines()
            next_offset = handle.tell()
    except OSError:
        return [], offset
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    return events, next_offset


def read_tool_category_audits(path: str) -> list[dict[str, Any]]:
    return [row for row in _read_ndjson(path, "audit.ndjson") if isinstance(row.get("tool"), str)]


__all__ = [
    "TOOL_CATEGORY_STATE_SCHEMA",
    "activate_tool_categories",
    "append_tool_category_audit",
    "append_tool_category_event",
    "creation_turn_write_denial_for_state",
    "creation_turn_write_tools_closed",
    "create_tool_category_state",
    "read_tool_category_audits",
    "read_tool_category_events",
    "read_tool_category_state",
    "remove_tool_category_state",
    "replace_tool_categories",
    "record_creation_turn_write_result",
]
