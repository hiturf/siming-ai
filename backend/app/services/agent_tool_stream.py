"""Collect one streamed model response with native tool calls."""

from __future__ import annotations

from typing import Any


async def collect_tool_turn(gateway: Any, **kwargs: Any) -> dict[str, Any]:
    stream = gateway.stream_chat_completion_with_tools(**kwargs)
    if not hasattr(stream, "__aiter__"):
        close = getattr(stream, "close", None)
        if callable(close):
            close()
        raise TypeError(
            "stream_chat_completion_with_tools 必须返回异步事件流"
        )

    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, int] | None = None
    async for event in stream:
        event_type = event.get("type")
        if event_type == "content_delta":
            content.append(str(event.get("delta") or ""))
        elif event_type == "tool_call_delta":
            index = int(event.get("index") or 0)
            call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if event.get("id"):
                call["id"] = str(event["id"])
            if event.get("name"):
                call["name"] = str(event["name"])
            if event.get("arguments_delta"):
                call["arguments"] += str(event["arguments_delta"])
        raw_usage = event.get("usage")
        if isinstance(raw_usage, dict) and raw_usage.get("prompt_tokens") is not None:
            usage = {
                key: max(0, int(raw_usage.get(key) or 0))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
    tool_calls = [
        {
            "id": call["id"] or f"agent-tool-{index}",
            "type": "function",
            "function": {"name": call["name"], "arguments": call["arguments"] or "{}"},
        }
        for index, call in sorted(calls.items())
        if call["name"]
    ]
    return {"content": "".join(content), "tool_calls": tool_calls, "usage": usage}


__all__ = ["collect_tool_turn"]
