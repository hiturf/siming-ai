"""Regression coverage for OpenAI-compatible streaming tool calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.deepseek_adapter import DeepSeekAdapter
from app.ai.gemini_adapter import GeminiAdapter
from app.ai.local_runtime_adapter import LocalRuntimeAdapter
from app.ai.openai_adapter import OpenAIAdapter
from app.ai.qwen_adapter import QwenAdapter
from app.services.agent_tool_stream import collect_tool_turn


def _async_stream(*chunks: object):
    async def generate():
        for chunk in chunks:
            yield chunk

    return generate()


def _tool_chunk(*, call_id: str | None, name: str | None, arguments: str | None):
    tool_call = SimpleNamespace(
        index=0,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    delta = SimpleNamespace(
        content=None,
        reasoning_content=None,
        tool_calls=[tool_call],
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason="tool_calls")],
        usage=None,
    )


def _adapter_with_stream(adapter_class, *chunks: object):
    adapter = adapter_class(api_key="test-key")
    if isinstance(adapter, LocalRuntimeAdapter):
        adapter._runtime_context = MagicMock(
            return_value=("http://127.0.0.1:8080/v1", {}),
        )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_async_stream(*chunks))
    adapter._get_client = MagicMock(return_value=client)
    return adapter


async def _collect(adapter, model: str) -> list[dict]:
    return [event async for event in adapter.stream_chat_completion_with_tools(
        messages=[{"role": "user", "content": "读取立项快照"}],
        model=model,
        tools=[{
            "type": "function",
            "function": {
                "name": "get_creation_snapshot",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        tool_choice="auto",
    )]


@pytest.mark.parametrize(
    ("adapter_class", "model"),
    [
        (OpenAIAdapter, "gpt-test"),
        (LocalRuntimeAdapter, "local-qwen-test"),
        (QwenAdapter, "qwen-test"),
        (GeminiAdapter, "gemini-test"),
        (DeepSeekAdapter, "deepseek-v4-flash"),
    ],
)
def test_openai_compatible_adapters_keep_name_when_short_arguments_share_one_frame(
    adapter_class,
    model: str,
):
    adapter = _adapter_with_stream(
        adapter_class,
        _tool_chunk(
            call_id="call-single-frame",
            name="get_creation_snapshot",
            arguments="{}",
        ),
    )

    events = asyncio.run(_collect(adapter, model))
    tool_events = [event for event in events if event["type"] == "tool_call_delta"]

    assert tool_events == [{
        "type": "tool_call_delta",
        "index": 0,
        "id": "call-single-frame",
        "name": "get_creation_snapshot",
        "arguments_delta": "{}",
    }]


def test_split_tool_frames_emit_the_name_once_without_losing_arguments():
    adapter = _adapter_with_stream(
        OpenAIAdapter,
        _tool_chunk(
            call_id="call-split-frame",
            name="get_creation_snapshot",
            arguments=None,
        ),
        _tool_chunk(call_id=None, name=None, arguments="{}"),
    )

    events = asyncio.run(_collect(adapter, "gpt-test"))
    tool_events = [event for event in events if event["type"] == "tool_call_delta"]

    assert [event["name"] for event in tool_events] == ["get_creation_snapshot", None]
    assert "".join(event["arguments_delta"] for event in tool_events) == "{}"


def test_single_frame_tool_call_survives_the_shared_agent_collector():
    adapter = _adapter_with_stream(
        OpenAIAdapter,
        _tool_chunk(
            call_id="call-single-frame",
            name="get_creation_snapshot",
            arguments="{}",
        ),
    )
    events = asyncio.run(_collect(adapter, "gpt-test"))

    class FakeGateway:
        @staticmethod
        async def stream_chat_completion_with_tools(**_kwargs):
            for event in events:
                yield event

    result = asyncio.run(collect_tool_turn(
        FakeGateway,
        messages=[],
        tools=[],
        model="gpt-test",
        temperature=0.2,
        max_tokens=None,
        timeout=30,
        retry=0,
        extra_body=None,
        tool_choice="auto",
    ))

    assert result["tool_calls"] == [{
        "id": "call-single-frame",
        "type": "function",
        "function": {"name": "get_creation_snapshot", "arguments": "{}"},
    }]
