"""Regression tests for retry boundaries after partial stream output."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.base import BaseAdapter
from app.core.exceptions import LLMError
from app.modules.model_runtime.infrastructure import gateway as gateway_module
from app.modules.model_runtime.infrastructure.gateway import LLMGateway


class TimeoutBoundaryAdapter(BaseAdapter):
    text_behaviors: list[str] = []
    tool_behaviors: list[str] = []
    text_messages: list[list[dict]] = []
    tool_messages: list[list[dict]] = []
    text_calls = 0
    tool_calls = 0

    @property
    def provider_name(self) -> str:
        return "timeout_probe"

    async def chat_completion(self, **_kwargs):
        return {"content": ""}

    async def stream_chat_completion(self, **kwargs):
        type(self).text_calls += 1
        type(self).text_messages.append(kwargs["messages"])
        behavior = type(self).text_behaviors.pop(0)
        if behavior == "timeout_before_output":
            raise TimeoutError("text timed out before output")
        if behavior == "timeout_after_output":
            yield "prefix"
            raise TimeoutError("text timed out after output")
        if behavior == "length_after_output":
            yield "prefix"
            self.last_stream_finish_reason = "length"
            return
        if behavior in {"resume_success", "resume_timeout"}:
            expected = kwargs["messages"][-1]["content"].split("：\n", 1)[1]
            yield expected[:17]
            yield expected[17:] + " suffix"
            if behavior == "resume_timeout":
                raise TimeoutError("resumed text timed out")
            return
        yield "complete"

    async def stream_chat_completion_with_tools(self, **kwargs):
        type(self).tool_calls += 1
        type(self).tool_messages.append(kwargs["messages"])
        behavior = type(self).tool_behaviors.pop(0)
        if behavior == "timeout_before_output":
            raise TimeoutError("tool stream timed out before output")
        if behavior == "timeout_after_output":
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-1",
                "name": "write",
                "arguments_delta": '{"value":',
            }
            raise TimeoutError("tool stream timed out after output")
        if behavior == "eof_after_complete_tool":
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-unverified",
                "name": "write",
                "arguments_delta": '{"value": 0}',
            }
            return
        if behavior == "tool_success":
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-2",
                "name": "write",
                "arguments_delta": '{"value": 1}',
            }
            yield {"type": "done", "finish_reason": "tool_calls", "usage": None}
            return
        yield {"type": "done", "finish_reason": "stop", "usage": None}


@pytest.fixture(autouse=True)
def isolated_gateway(monkeypatch):
    TimeoutBoundaryAdapter.text_behaviors = []
    TimeoutBoundaryAdapter.tool_behaviors = []
    TimeoutBoundaryAdapter.text_messages = []
    TimeoutBoundaryAdapter.tool_messages = []
    TimeoutBoundaryAdapter.text_calls = 0
    TimeoutBoundaryAdapter.tool_calls = 0
    config = SimpleNamespace(
        api_key="test-key",
        base_url="http://127.0.0.1:1/v1",
        provider="timeout_probe",
        api_protocol="chat_completions",
        cli_command=None,
        cli_args=None,
    )
    monkeypatch.setattr(
        LLMGateway,
        "_parse_model",
        staticmethod(lambda _model: ("timeout_probe", "model")),
    )
    monkeypatch.setattr(
        LLMGateway,
        "_load_config",
        staticmethod(lambda _provider: config),
    )
    monkeypatch.setattr(
        LLMGateway,
        "_get_adapter",
        staticmethod(lambda _provider: TimeoutBoundaryAdapter),
    )
    monkeypatch.setattr(gateway_module.asyncio, "sleep", AsyncMock())


async def _collect_text(
    *,
    retry: int,
    resume: int = 0,
    on_resume=None,
) -> tuple[list[str], LLMError | None]:
    received: list[str] = []
    try:
        async for item in LLMGateway.stream_chat_completion(
            messages=[{"role": "user", "content": "probe"}],
            model="timeout_probe:model",
            timeout=1,
            retry=retry,
            resume=resume,
            on_resume=on_resume,
        ):
            received.append(item)
    except LLMError as exc:
        return received, exc
    return received, None


async def _collect_tools(*, retry: int, resume: int = 0) -> tuple[list[dict], LLMError | None]:
    received: list[dict] = []
    try:
        async for item in LLMGateway.stream_chat_completion_with_tools(
            messages=[{"role": "user", "content": "probe"}],
            model="timeout_probe:model",
            timeout=1,
            retry=retry,
            resume=resume,
            tools=[{
                "type": "function",
                "function": {"name": "write", "parameters": {"type": "object"}},
            }],
        ):
            received.append(item)
    except LLMError as exc:
        return received, exc
    return received, None


def test_text_stream_does_not_restart_after_output_when_resume_is_disabled():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_after_output"] * 3

    received, error = asyncio.run(_collect_text(retry=2))

    assert received == ["prefix"]
    assert isinstance(error, LLMError)
    assert "流式请求超时" in str(error)
    assert TimeoutBoundaryAdapter.text_calls == 1


def test_tool_stream_does_not_expose_or_restart_partial_arguments_when_resume_is_disabled():
    TimeoutBoundaryAdapter.tool_behaviors = ["timeout_after_output"] * 3

    received, error = asyncio.run(_collect_tools(retry=2))

    assert received == []
    assert isinstance(error, LLMError)
    assert "流式请求超时" in str(error)
    assert TimeoutBoundaryAdapter.tool_calls == 1


def test_text_stream_still_retries_timeout_before_first_output():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_before_output", "success"]

    received, error = asyncio.run(_collect_text(retry=1))

    assert received == ["complete"]
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 2


def test_tool_stream_still_retries_timeout_before_first_output():
    TimeoutBoundaryAdapter.tool_behaviors = ["timeout_before_output", "success"]

    received, error = asyncio.run(_collect_tools(retry=1))

    assert [item["type"] for item in received] == ["done"]
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2


def test_text_stream_resumes_from_verified_checkpoint_without_duplicate_prefix():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_after_output", "resume_success"]
    on_resume = AsyncMock()

    received, error = asyncio.run(_collect_text(retry=0, resume=2, on_resume=on_resume))

    assert "".join(received) == "prefix suffix"
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 2
    assert TimeoutBoundaryAdapter.text_messages[1][-2] == {
        "role": "assistant",
        "content": "prefix",
    }
    on_resume.assert_awaited_once()
    assert on_resume.await_args.args[0]["checkpoint_chars"] == len("prefix")


def test_text_stream_resumes_when_provider_stops_at_single_call_token_limit():
    TimeoutBoundaryAdapter.text_behaviors = ["length_after_output", "resume_success"]

    received, error = asyncio.run(_collect_text(retry=0, resume=2))

    assert "".join(received) == "prefix suffix"
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 2


def test_text_stream_can_resume_more_than_once_without_repeating_committed_text():
    TimeoutBoundaryAdapter.text_behaviors = [
        "timeout_after_output",
        "resume_timeout",
        "resume_success",
    ]

    received, error = asyncio.run(_collect_text(retry=0, resume=2))

    assert "".join(received) == "prefix suffix suffix"
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 3
    assert TimeoutBoundaryAdapter.text_messages[2][-2]["content"] == "prefix suffix"


def test_tool_stream_discards_partial_json_and_replans_one_complete_call():
    TimeoutBoundaryAdapter.tool_behaviors = ["timeout_after_output", "tool_success"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=2))

    tool_deltas = [item for item in received if item["type"] == "tool_call_delta"]
    assert [item["arguments_delta"] for item in tool_deltas] == ['{"value": 1}']
    assert [item["type"] for item in received] == ["tool_call_delta", "done"]
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
    assert "未完成工具参数都已被丢弃" in TimeoutBoundaryAdapter.tool_messages[1][0]["content"]


def test_tool_stream_requires_terminal_frame_before_exposing_complete_arguments():
    TimeoutBoundaryAdapter.tool_behaviors = ["eof_after_complete_tool", "tool_success"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=2))

    tool_deltas = [item for item in received if item["type"] == "tool_call_delta"]
    assert [item["id"] for item in tool_deltas] == ["call-2"]
    assert [item["arguments_delta"] for item in tool_deltas] == ['{"value": 1}']
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
