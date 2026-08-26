"""Persistence and presentation helpers for project-assistant turns."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.core.utils import utc_isoformat
from app.services.operation_runtime import record_operation_signal
from app.services.workspace.run_log import mark_assistant_run, run_payload


def _compact_workspace_detail(value: object, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _workspace_result_summary(result: dict) -> str:
    tool = str(result.get("tool") or "tool")
    status = str(result.get("status") or "ok")
    detail = _compact_workspace_detail(result.get("detail") or "")
    prefix = f"{tool}（{status}）"
    return f"{prefix}：{detail}" if detail else prefix


def _build_workspace_final_reply(
    final_reply: str,
    *,
    applied_actions: list[dict],
    tool_logs: list[dict],
    searched_context: list[dict],
) -> str:
    reply = str(final_reply or "").strip()
    if reply:
        return reply

    if applied_actions:
        lines = [
            f"本轮已执行 {len(applied_actions)} 个工具操作，但模型没有给出最终文字回复。",
            "",
            "执行结果：",
        ]
        lines.extend(f"- {_workspace_result_summary(action)}" for action in applied_actions[:5])
        if len(applied_actions) > 5:
            lines.append(f"- 另有 {len(applied_actions) - 5} 个结果已省略")
        return "\n".join(lines)

    if tool_logs:
        lines = ["本轮已调用工具，但模型没有给出最终文字回复。", "", "工具结果："]
        lines.extend(f"- {_workspace_result_summary(log)}" for log in tool_logs[:5])
        if len(tool_logs) > 5:
            lines.append(f"- 另有 {len(tool_logs) - 5} 条工具日志已省略")
        return "\n".join(lines)

    if searched_context:
        lines = ["本轮已读取相关资料，但模型没有给出最终文字回复。", "", "已读取："]
        for item in searched_context[:5]:
            tool = str(item.get("tool") or "search")
            detail = _compact_workspace_detail(item.get("detail") or "")
            data = item.get("data")
            count = len(data) if isinstance(data, list) else 0
            suffix = detail or (f"{count} 条结果" if count else "有结果")
            lines.append(f"- {tool}：{suffix}")
        if len(searched_context) > 5:
            lines.append(f"- 另有 {len(searched_context) - 5} 条检索上下文已省略")
        lines.extend([
            "",
            "请重试一次；如果连续出现，建议在系统设置里测试当前模型/CLI 的流式输出和工具调用能力。",
        ])
        return "\n".join(lines)

    return "我没有收到模型的文字回复，也没有执行任何工具。请重试一次，或在系统设置里测试当前模型/CLI 是否支持项目助手的流式输出和工具调用。"


def _workspace_outcome(
    raw_reply: str,
    *,
    applied_actions: list[dict],
    tool_logs: list[dict],
    searched_context: list[dict],
    failed_logs: list[dict] | None = None,
) -> str:
    """Return a stable user-facing outcome for an assistant turn."""
    if failed_logs:
        return "partial_success" if applied_actions else "failed"
    if str(raw_reply or "").strip():
        return "completed_with_reply"
    if applied_actions or tool_logs or searched_context:
        return "completed_with_tools"
    return "empty_response"


def _assistant_conversation_to_dict(
    conversation: Any,
    message_count: Optional[int] = None,
) -> dict:
    return {
        "id": conversation.id,
        "project_id": conversation.project_id,
        "title": conversation.title,
        "scope": conversation.scope,
        "model": conversation.model,
        "message_count": message_count,
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
    }


def _assistant_message_to_dict(message: Any) -> dict:
    payload = None
    if message.payload_json:
        try:
            payload = json.loads(message.payload_json)
        except Exception:
            payload = None
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "payload": payload,
        "status": message.status,
        "created_at": utc_isoformat(message.created_at),
        "updated_at": utc_isoformat(message.updated_at),
    }


@dataclass
class WorkspaceTurnTelemetry:
    reasoning_parts: list[str] = field(default_factory=list)
    last_reasoning_iteration: int = 0
    last_operation_report_at: float = 0.0

    def report_model_activity(
        self,
        assistant_run: Any,
        text: str,
        *,
        signal: str = "output",
        message: str = "模型正在生成回复",
    ) -> None:
        if not assistant_run or not assistant_run.operation_id:
            return
        now = time.monotonic()
        if now - self.last_operation_report_at < 2:
            return
        self.last_operation_report_at = now
        record_operation_signal(
            assistant_run.operation_id,
            signal,
            {"output_chars": len(text or "")},
            message=message,
        )

    def record_reasoning_delta(self, text: str, iteration: int) -> str:
        """Keep the persisted transcript byte-for-byte aligned with SSE deltas."""
        delta = str(text or "")
        if not delta:
            return ""
        prefix = "\n\n" if self.reasoning_parts and self.last_reasoning_iteration != iteration else ""
        visible_delta = f"{prefix}{delta}"
        self.reasoning_parts.append(visible_delta)
        self.last_reasoning_iteration = iteration
        return visible_delta


def finalize_workspace_assistant_turn(
    db: Session,
    *,
    assistant_run: Any,
    assistant_message: Any,
    conversation: Any,
    final_reply: str,
    applied_actions: list[dict],
    tool_logs: list[dict],
    searched_context: list[dict],
    final_model: str,
    final_usage: Any,
    reasoning_content: str,
) -> dict[str, Any]:
    failed_logs = [
        log for log in tool_logs
        if str(log.get("status") or "").lower() == "error"
    ]
    final_reply_for_save = _build_workspace_final_reply(
        final_reply,
        applied_actions=applied_actions,
        tool_logs=tool_logs,
        searched_context=searched_context,
    )
    if failed_logs:
        failed_text = "；".join(
            f"{log.get('tool')}: {log.get('detail') or '执行失败'}"
            for log in failed_logs[:3]
        )
        final_reply_for_save = (
            f"{final_reply_for_save}\n\n注意：本轮有工具执行失败，相关数据可能未保存：{failed_text}"
        ).strip()
    outcome = _workspace_outcome(
        final_reply,
        applied_actions=applied_actions,
        tool_logs=tool_logs,
        searched_context=searched_context,
        failed_logs=failed_logs,
    )
    response_payload: dict[str, Any] = {
        "reply": final_reply_for_save,
        "reasoning_content": reasoning_content,
        "outcome": outcome,
        "actions": [],
        "applied_actions": applied_actions,
        "tool_logs": tool_logs,
        "searched_context": searched_context,
        "scope": "project",
        "model": final_model,
        "usage": final_usage,
    }
    if assistant_run:
        response_payload["run"] = run_payload(assistant_run)
    assistant_message.content = response_payload["reply"]
    assistant_message.payload_json = json.dumps(response_payload, ensure_ascii=False)
    assistant_message.status = "completed"
    assistant_message.updated_at = datetime.utcnow()
    conversation.updated_at = datetime.utcnow()
    commit_session(db)
    mark_assistant_run(
        db,
        assistant_run,
        status="error" if outcome == "failed" else "completed",
        phase="error" if outcome == "failed" else outcome,
        final_reply=final_reply_for_save,
        outcome=outcome,
    )
    if assistant_run:
        db.refresh(assistant_run)
        response_payload["run"] = run_payload(assistant_run)
        assistant_message.payload_json = json.dumps(response_payload, ensure_ascii=False)
        commit_session(db)
    db.refresh(assistant_message)
    db.refresh(conversation)
    response_payload["message"] = _assistant_message_to_dict(assistant_message)
    response_payload["conversation"] = _assistant_conversation_to_dict(conversation)
    return response_payload
