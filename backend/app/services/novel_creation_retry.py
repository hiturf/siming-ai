"""Frozen-input selection for durable creation retries."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.services.operation_runtime import input_snapshot_hash


@dataclass(frozen=True)
class CreationRetryInput:
    request: dict[str, Any]
    snapshot: dict[str, Any]
    revision: int
    snapshot_hash: str


def select_creation_retry_input(previous: Any, session: Any, *, use_latest: bool) -> CreationRetryInput:
    request = dict(previous.request_json or {})
    original_snapshot = deepcopy(request.get("input_snapshot"))
    original_revision = int(
        previous.input_revision
        if previous.input_revision is not None
        else request.get("input_revision") or 0
    )
    for key in (
        "input_snapshot",
        "input_snapshot_hash",
        "input_revision",
        "operation_id",
        "context_manifest_id",
    ):
        request.pop(key, None)
    request["retry_of_run_id"] = previous.id
    request["retry_mode"] = "latest_draft" if use_latest else "original_input"
    current = session.draft_json if isinstance(session.draft_json, dict) else {}
    if use_latest or not isinstance(original_snapshot, dict):
        snapshot, revision = deepcopy(current), int(session.revision or 0)
    else:
        snapshot, revision = original_snapshot, original_revision
    return CreationRetryInput(request, snapshot, revision, input_snapshot_hash(snapshot))
