"""Small runtime helpers for novel-creation stage orchestration."""
from __future__ import annotations

from typing import Any

from app.services.novel_creation_workspace import (
    serialize_run,
    serialize_session,
)


async def generate_stage_data(
    session: Any,
    *,
    stage: str,
    baseline: dict[str, Any],
    model: str,
    use_model: bool,
    manifest: Any,
    working_draft: dict[str, Any],
    enhance: Any,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if not use_model or not model:
        raise RuntimeError("当前没有可用于立项生成的模型")
    enhanced = await enhance(
        session,
        stage,
        baseline,
        model,
        context_manifest=manifest,
        input_snapshot=working_draft,
    )
    if isinstance(enhanced, tuple):
        data, metadata = enhanced
    else:
        data, metadata = enhanced, {"attempt": 1, "result_mode": "model", "warning": None}
    return data, "model" if metadata.get("result_mode") == "model" else "model_repaired", metadata


def stage_tool_result(status: str, detail: str, run: Any, session: Any) -> dict[str, Any]:
    return {
        "tool": "generate_creation_artifact",
        "status": status,
        "detail": detail,
        "data": {"run": serialize_run(run), "session": serialize_session(session)},
    }
