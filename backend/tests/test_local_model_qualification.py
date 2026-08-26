"""Task-level local model qualification contracts."""

from __future__ import annotations

import asyncio
import json

from app.services.local_runtime.qualification import qualify_local_model


def test_qualification_requires_real_consistency_cataloging_and_tool_work():
    async def complete(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        if "AUTHORITATIVE_RECORD" in prompt:
            content = json.dumps(
                {
                    "record_id": "NC-2500-1000",
                    "target_words": 2_500_000,
                    "target_chapters": 1_000,
                    "constraint_status": "confirmed",
                    "next_action": "continue_writing",
                }
            )
        elif "completed_beat" in prompt:
            content = (
                '{"completed_beat":{"chapter_number":73,"beat":"公开承认继承人"}}\n'
                '{"revealed_clue":{"chapter_number":73,"clue":"钥匙来自归墟旧城"}}'
            )
        else:
            content = '{"tool":"get_project_info","arguments":{},"reason":"先读取事实"}'
        return {"content": content}

    result = asyncio.run(
        qualify_local_model("test-model", 8192, completion=complete)
    )

    assert result["rating"] == "qualified"
    assert result["passed"] is True
    assert result["passed_count"] == 3
    assert all(case["passed"] for case in result["cases"])
    assert result["cases"][0]["input_characters"] >= 3500


def test_qualification_reports_partial_models_as_limited():
    calls = 0

    async def complete(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"content": "{}"}
        if calls == 2:
            return {
                "content": (
                    '{"completed_beat":{"chapter_number":73,"beat":"完成"}}\n'
                    '{"revealed_clue":{"chapter_number":73,"clue":"揭示"}}'
                )
            }
        return {"content": '{"tool":"create_character","arguments":{}}'}

    result = asyncio.run(
        qualify_local_model("partial-model", 8192, completion=complete)
    )

    assert result["rating"] == "limited"
    assert result["passed"] is False
    assert result["passed_count"] == 1
