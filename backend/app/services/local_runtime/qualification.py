"""Task-level qualification for a managed local language model."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ...modules.model_runtime.application.execution import model_executor

QUALIFICATION_VERSION = "2026-08-09.1"

Completion = Callable[..., Awaitable[dict[str, Any]]]


def _response_text(result: dict[str, Any]) -> str:
    return str(result.get("content") or result.get("reasoning_content") or "").strip()


def _json_value(text: str) -> Any:
    cleaned = str(text or "").strip()
    fence = re.match(r"^```(?:json|jsonl)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


def _long_context_prompt(context_length: int) -> str:
    target_chars = min(12_000, max(3_500, int(context_length * 0.55)))
    neutral = [
        f"资料片段 {index:04d}：本段是旧稿索引，仅用于测试长上下文定位；不构成当前立项约束。"
        for index in range(1, 260)
    ]
    canonical = (
        "\n[AUTHORITATIVE_RECORD id=NC-2500-1000 status=confirmed]\n"
        "目标总字数：2500000；目标章节数：1000；状态：confirmed；"
        "正文阶段应继续写作，不得重新要求确认。\n[/AUTHORITATIVE_RECORD]\n"
    )
    obsolete = (
        "\n[DRAFT_NOTE status=obsolete]曾讨论 600000 字、240 章；该草案已废弃，"
        "不得覆盖 authoritative record。[/DRAFT_NOTE]\n"
    )
    pieces = neutral[:24] + [canonical] + neutral[24:]
    prompt = "\n".join(pieces)
    while len(prompt) < target_chars:
        prompt += "\n" + "\n".join(neutral)
    prompt = prompt[:target_chars] + obsolete
    return (
        "下面是同一作品的上下文。请只信任 status=confirmed 的 AUTHORITATIVE_RECORD，"
        "忽略 obsolete 草案。阅读完毕后只输出一个 JSON 对象，不要解释："
        '{"record_id":"...","target_words":0,"target_chapters":0,'
        '"constraint_status":"...","next_action":"..."}\n\n'
        + prompt
    )


def _cataloging_prompt() -> str:
    return """你正在执行作品建档。把下面事实严格输出为两行 JSONL；不要 Markdown，不要解释。
每行只能有一个顶层键，第一行键为 completed_beat，第二行键为 revealed_clue。
每个值必须包含 chapter_number=73 和对应的 beat 或 clue 字段。

正文事实：第73章，陆老爷子在祠堂公开承认陆沉是继承人；随后众人确认青铜钥匙来自归墟旧城。"""


def _tool_decision_prompt() -> str:
    return """你是项目助手。用户问：“这部作品立项时定了多少字、多少章？”
当前消息没有提供这两个数值。可用工具只有：
- get_project_info({})：读取当前项目及关联立项约束
- update_project_info({...})：修改项目
- save_external_chapter_draft({...})：保存未入库章节草稿

只输出 JSON，不要猜测数值：
{"tool":"工具名","arguments":{},"reason":"一句话原因"}"""


def _evaluate_long_context(text: str) -> tuple[bool, str]:
    try:
        data = _json_value(text)
    except Exception:
        return False, "未返回可解析的 JSON"
    passed = (
        data.get("record_id") == "NC-2500-1000"
        and int(data.get("target_words") or 0) == 2_500_000
        and int(data.get("target_chapters") or 0) == 1_000
        and str(data.get("constraint_status") or "").lower() == "confirmed"
        and str(data.get("next_action") or "").lower() == "continue_writing"
    )
    return (
        (True, "能在冲突草案中找回已确认的立项事实")
        if passed
        else (False, "未能保持 250 万字 / 1000 章的已确认事实")
    )


def _evaluate_cataloging(text: str) -> tuple[bool, str]:
    cleaned = str(text or "").strip()
    fence = re.match(r"^```(?:jsonl|json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        rows = [json.loads(line) for line in cleaned.splitlines() if line.strip()]
    except Exception:
        return False, "JSONL 无法逐行解析"
    expected = ("completed_beat", "revealed_clue")
    if len(rows) != 2 or any(list(row) != [key] for row, key in zip(rows, expected, strict=True)):
        return False, "未遵守单键包装的两行 JSONL 契约"
    payloads = [rows[index][key] for index, key in enumerate(expected)]
    passed = all(
        isinstance(payload, dict) and int(payload.get("chapter_number") or 0) == 73
        for payload in payloads
    ) and bool(payloads[0].get("beat")) and bool(payloads[1].get("clue"))
    return (
        (True, "能生成建档解析器要求的单键 JSONL")
        if passed
        else (False, "结构存在，但章节号或必填事实字段缺失")
    )


def _evaluate_tool_decision(text: str) -> tuple[bool, str]:
    try:
        data = _json_value(text)
    except Exception:
        return False, "未返回可解析的工具决策 JSON"
    passed = data.get("tool") == "get_project_info" and data.get("arguments") == {}
    return (
        (True, "信息不足时会先读取项目事实，不会猜测或写入")
        if passed
        else (False, "未选择只读的 get_project_info 工具")
    )


async def qualify_local_model(
    model_key: str,
    context_length: int,
    *,
    completion: Completion | None = None,
) -> dict[str, Any]:
    """Run deterministic tasks that mirror the product's critical workflows."""

    complete = completion or model_executor.chat_completion
    cases = [
        (
            "creation_consistency",
            "长上下文立项一致性",
            "planning",
            _long_context_prompt(context_length),
            _evaluate_long_context,
        ),
        (
            "cataloging_contract",
            "作品建档结构契约",
            "cataloging",
            _cataloging_prompt(),
            _evaluate_cataloging,
        ),
        (
            "tool_decision",
            "项目助手工具决策",
            "chat",
            _tool_decision_prompt(),
            _evaluate_tool_decision,
        ),
    ]
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    for case_id, label, task_type, prompt, evaluator in cases:
        case_started = time.perf_counter()
        output = ""
        try:
            response = await complete(
                messages=[
                    {
                        "role": "system",
                        "content": "严格执行任务，并完全遵守用户指定的输出格式。",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=f"local_llama_cpp:{model_key}",
                temperature=0,
                max_tokens=320,
                extra_body={
                    "moshu_task_type": task_type,
                    "moshu_context_length": context_length,
                },
                retry=0,
                timeout=600,
            )
            output = _response_text(response)
            passed, detail = evaluator(output)
        except Exception as exc:
            passed, detail = False, f"执行失败：{exc}"
        results.append(
            {
                "id": case_id,
                "label": label,
                "passed": passed,
                "detail": detail,
                "elapsed_seconds": round(time.perf_counter() - case_started, 2),
                "output_preview": output[:800],
                "input_characters": len(prompt),
            }
        )
    passed_count = sum(1 for item in results if item["passed"])
    if passed_count == len(results):
        rating = "qualified"
    elif passed_count:
        rating = "limited"
    else:
        rating = "failed"
    return {
        "version": QUALIFICATION_VERSION,
        "model_key": model_key,
        "context_length": context_length,
        "rating": rating,
        "passed": passed_count == len(results),
        "passed_count": passed_count,
        "total_count": len(results),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "cases": results,
    }


__all__ = ["QUALIFICATION_VERSION", "qualify_local_model"]
