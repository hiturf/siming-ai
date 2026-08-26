"""Canonical JSON persistence contract for workspace assistant run steps."""
from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_RESULT_JSON_CHARS = 80_000
RUN_LOG_META_KEY = "_siming_run_log"
RUN_LOG_PAYLOAD_VERSION = 1
LEGACY_TRUNCATION_SUFFIX = "...[truncated]"

_UNRECOVERABLE_REQUEST_MESSAGE = (
    "该步骤的历史请求参数不完整，无法安全重试；请重新发起原任务。"
)


class UnrecoverableStepRequest(ValueError):
    """Raised when persisted tool arguments cannot be replayed exactly."""


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def serialize_step_request(data: Any) -> str:
    """Serialize replayable tool arguments without truncation.

    A retry must receive the exact argument object selected by the model.  It is
    therefore unsafe to shorten, summarize, or replace this payload.
    """

    if not isinstance(data, dict):
        raise ValueError("步骤请求参数必须是 JSON 对象")
    try:
        # Tool arguments originate from the model's JSON protocol.  Reject
        # non-JSON values instead of silently stringifying them, because such a
        # conversion would no longer be an exact replay of the original call.
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("步骤请求参数无法完整序列化，未执行该步骤") from exc


def _truncated_result_envelope(text: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("结果日志长度上限必须大于 0")

    envelope: dict[str, Any] = {
        RUN_LOG_META_KEY: {
            "version": RUN_LOG_PAYLOAD_VERSION,
            "kind": "truncated_result",
        },
        "_truncated": True,
        "original_chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "preview": "",
    }
    smallest = _dumps(envelope)
    if len(smallest) > max_chars:
        raise ValueError("结果日志长度上限不足以保存截断元数据")

    # Escaping quotes, slashes and control characters can make the encoded
    # preview much longer than its source slice.  Binary search guarantees the
    # final database value respects the configured character limit and remains
    # valid JSON for every input.
    best = smallest
    low = 0
    high = min(len(text), max_chars)
    while low <= high:
        length = (low + high) // 2
        envelope["preview"] = text[:length]
        candidate = _dumps(envelope)
        if len(candidate) <= max_chars:
            best = candidate
            low = length + 1
        else:
            high = length - 1
    return best


def serialize_step_result(
    data: Any,
    *,
    max_chars: int = MAX_RESULT_JSON_CHARS,
) -> str:
    """Serialize a result as valid JSON, using a versioned envelope if large."""

    try:
        text = _dumps(data)
    except (TypeError, ValueError, RecursionError):
        return _dumps(
            {
                RUN_LOG_META_KEY: {
                    "version": RUN_LOG_PAYLOAD_VERSION,
                    "kind": "unavailable_result",
                },
                "_unavailable": True,
                "reason": "serialization_failed",
                "value_type": type(data).__name__,
            }
        )
    if len(text) <= max_chars:
        return text
    return _truncated_result_envelope(text, max_chars=max_chars)


def _payload_kind(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    metadata = value.get(RUN_LOG_META_KEY)
    if not isinstance(metadata, dict):
        return None
    if metadata.get("version") != RUN_LOG_PAYLOAD_VERSION:
        return None
    kind = metadata.get("kind")
    return str(kind) if kind else None


def deserialize_step_request(raw: str | None) -> dict[str, Any]:
    """Load exact replay arguments or reject the step before tool execution."""

    if raw is None or raw == "":
        return {}
    if raw.endswith(LEGACY_TRUNCATION_SUFFIX):
        raise UnrecoverableStepRequest(_UNRECOVERABLE_REQUEST_MESSAGE)
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise UnrecoverableStepRequest(
            "该步骤的请求参数记录已损坏，无法安全重试；请重新发起原任务。"
        ) from exc
    if _payload_kind(value) == "unrecoverable_request":
        raise UnrecoverableStepRequest(_UNRECOVERABLE_REQUEST_MESSAGE)
    if not isinstance(value, dict):
        raise UnrecoverableStepRequest(
            "该步骤的请求参数不是 JSON 对象，无法安全重试；请重新发起原任务。"
        )
    return value


def step_request_retry_block_reason(raw: str | None) -> str | None:
    """Return a user-facing reason when a persisted request cannot be replayed."""

    try:
        deserialize_step_request(raw)
    except UnrecoverableStepRequest as exc:
        return str(exc)
    return None


def deserialize_step_value_for_display(raw: str | None) -> Any:
    """Decode a persisted value without sending malformed legacy JSON to clients."""

    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        preview = raw
        if preview.endswith(LEGACY_TRUNCATION_SUFFIX):
            preview = preview[: -len(LEGACY_TRUNCATION_SUFFIX)]
        return {
            RUN_LOG_META_KEY: {
                "version": RUN_LOG_PAYLOAD_VERSION,
                "kind": "corrupt_legacy_payload",
            },
            "_unavailable": True,
            "stored_chars": len(raw),
            "stored_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "preview": preview[:4096],
        }


__all__ = [
    "LEGACY_TRUNCATION_SUFFIX",
    "MAX_RESULT_JSON_CHARS",
    "RUN_LOG_META_KEY",
    "RUN_LOG_PAYLOAD_VERSION",
    "UnrecoverableStepRequest",
    "deserialize_step_request",
    "deserialize_step_value_for_display",
    "serialize_step_request",
    "serialize_step_result",
    "step_request_retry_block_reason",
]
