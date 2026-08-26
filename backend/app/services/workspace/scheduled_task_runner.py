"""Workspace implementation of the scheduler's task-runner port."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.orm import Session

from ...architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
    tool_category_controller_schema,
    tool_names_for_categories,
)
from ...database.models import ScheduledTask
from ...modules.model_runtime.application.execution import model_executor as LLMGateway
from ..agent_tool_stream import collect_tool_turn
from . import executor as workspace_executor
from .registry import registry
from .run_step_payloads import serialize_step_result
from .tool_schemas import build_workspace_tool_schemas

MAX_SCHEDULED_AGENT_STEPS = 10
MAX_SCHEDULED_TOOL_CALLS_PER_STEP = 12
MAX_TOOL_RESULT_CHARS = 12_000


def _authorized_tool_names(task: ScheduledTask) -> set[str]:
    names = {tool.name for tool in registry.list_for_scheduler()}
    raw_policy = task.tool_policy if isinstance(task.tool_policy, list) else []
    policy = {str(name).strip() for name in raw_policy if str(name).strip()}
    return names & policy if raw_policy else names


def _active_tool_names(
    authorized_names: set[str],
    active_categories: tuple[str, ...],
) -> set[str]:
    return authorized_names & set(tool_names_for_categories(active_categories))


def _tool_schemas(
    authorized_names: set[str],
    active_categories: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        tool_category_controller_schema(),
        *build_workspace_tool_schemas(
            sorted(_active_tool_names(authorized_names, active_categories))
        ),
    ]


def _category_result(
    arguments: dict[str, Any],
    authorized_names: set[str],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    try:
        categories = normalize_tool_categories(arguments.get("enabled_categories"))
    except ValueError as exc:
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "error",
            "detail": str(exc),
            "data": None,
        }, None
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
    available = _active_tool_names(authorized_names, categories)
    detail = (
        f"已准备{'、'.join(labels)}能力，共 {len(available)} 项定时任务工具"
        if labels
        else "已关闭全部业务工具"
    )
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": {
            "enabled_categories": list(categories),
            "available_tool_count": len(available),
        },
    }, categories


def _tool_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise ValueError("工具调用缺少 function 对象")
    raw_arguments = function.get("arguments", "{}")
    if isinstance(raw_arguments, dict):
        arguments = dict(raw_arguments)
    elif isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("工具参数不是合法 JSON") from exc
    else:
        raise ValueError("工具参数必须是 JSON 对象")
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return arguments


def _tool_message(tool_call: dict[str, Any], result: dict[str, Any]) -> dict[str, str]:
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id") or ""),
        "content": serialize_step_result(result, max_chars=MAX_TOOL_RESULT_CHARS),
    }


def run_workspace_scheduled_task(db: Session, task: ScheduledTask) -> str:
    """Run one scheduled prompt through the normal workspace tool chain."""
    system_parts = [
        "你是一个定时任务执行助手。请根据用户的提示完成任务。",
        (
            "第一模型步骤只开放 set_tool_categories，必须先调用它选择所需能力；"
            "类别从下一模型步骤生效，调用控制工具后当前步骤立即结束。"
        ),
        "只调用本步骤实际提供的工具；工具结果失败时不得声称任务已完成。",
    ]
    if isinstance(task.tool_policy, list) and task.tool_policy:
        policy_names = [str(name).strip() for name in task.tool_policy]
        system_parts.append(f"本任务授权工具：{', '.join(policy_names)}")

    messages = [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": task.prompt},
    ]

    authorized_names = _authorized_tool_names(task)

    async def run_agent_loop() -> str:
        active_categories: tuple[str, ...] = ()
        category_selected = False
        for _turn in range(MAX_SCHEDULED_AGENT_STEPS):
            active_names = _active_tool_names(authorized_names, active_categories)
            result = await collect_tool_turn(
                LLMGateway,
                messages=messages,
                tools=_tool_schemas(authorized_names, active_categories),
                tool_choice="required" if not category_selected else "auto",
                model=None,
                temperature=0.3,
                max_tokens=4000,
                timeout=120,
            )
            content = str(result.get("content") or "")
            tool_calls = (
                list(result.get("tool_calls") or [])
                if isinstance(result.get("tool_calls"), list)
                else []
            )
            controller_calls = [
                call
                for call in tool_calls
                if isinstance(call, dict)
                and isinstance(call.get("function"), dict)
                and call["function"].get("name") == TOOL_CATEGORY_CONTROLLER
            ]
            if controller_calls:
                # Category replacement ends this model step.  Ignore any
                # business calls emitted in the same batch so calls/results
                # remain paired and the new category only affects next step.
                tool_calls = controller_calls[:1]
            elif not category_selected:
                raise RuntimeError(
                    "模型没有调用本步骤唯一开放的 set_tool_categories，定时任务已停止"
                )

            if not tool_calls:
                return content.strip() or "任务执行完成"

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls[:MAX_SCHEDULED_TOOL_CALLS_PER_STEP],
                }
            )

            for tool_call in tool_calls[:MAX_SCHEDULED_TOOL_CALLS_PER_STEP]:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                tool_name = str(function.get("name") or "") if isinstance(function, dict) else ""
                try:
                    arguments = _tool_arguments(tool_call)
                except ValueError as exc:
                    tool_result = {
                        "tool": tool_name,
                        "status": "error",
                        "detail": str(exc),
                        "data": None,
                    }
                else:
                    if tool_name == TOOL_CATEGORY_CONTROLLER:
                        tool_result, replacement = _category_result(arguments, authorized_names)
                        if replacement is not None:
                            active_categories = replacement
                            category_selected = True
                    elif tool_name not in active_names:
                        tool_result = {
                            "tool": tool_name,
                            "status": "skipped",
                            "detail": "该工具不在当前开放类别或定时任务授权范围内",
                            "data": None,
                        }
                    else:
                        tool_result = await workspace_executor.execute_workspace_action(
                            db,
                            task.project_id,
                            {"tool": tool_name, "arguments": arguments},
                        )
                messages.append(_tool_message(tool_call, tool_result))

                if tool_name == TOOL_CATEGORY_CONTROLLER:
                    break

        raise RuntimeError("定时任务超过最大 Agent 步骤数，已停止继续执行")

    try:
        return asyncio.run(run_agent_loop())
    except Exception as exc:
        raise RuntimeError(f"Agent execution failed: {exc}") from exc


__all__ = ["run_workspace_scheduled_task"]
