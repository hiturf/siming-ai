"""Connection lifecycle tests for detached workspace-assistant execution."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock, patch

from app.services.workspace.assistant_stream_runtime import detached_assistant_stream


class _DummySession:
    def close(self) -> None:
        return None


class DetachedAssistantStreamTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_connected_subscriber_never_loses_run_or_terminal_events(self) -> None:
        async def source(_db):
            yield (
                'data: {"type":"run","run":{"id":"run-1",'
                '"operation_id":"operation-1"}}\n\n'
            )
            for index in range(300):
                yield f'data: {{"type":"reasoning_delta","delta":"{index}"}}\n\n'
            yield 'data: {"type":"complete","data":{"reply":"done"}}\n\n'
            yield 'data: [DONE]\n\n'

        with patch(
            "app.services.workspace.assistant_stream_runtime.SessionLocal",
            return_value=_DummySession(),
        ):
            chunks = [chunk async for chunk in detached_assistant_stream(source)]

        self.assertEqual(len(chunks), 303)
        self.assertIn('"type":"run"', chunks[0])
        self.assertIn('"type":"complete"', chunks[-2])
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")

    async def test_client_disconnect_does_not_cancel_the_producer(self) -> None:
        completed = asyncio.Event()

        async def source(_db):
            yield 'data: {"type":"status","message":"started"}\n\n'
            await asyncio.sleep(0)
            completed.set()
            yield 'data: {"type":"complete"}\n\n'

        with patch(
            "app.services.workspace.assistant_stream_runtime.SessionLocal",
            return_value=_DummySession(),
        ):
            stream = detached_assistant_stream(source)
            first = await anext(stream)
            self.assertIn("started", first)
            await stream.aclose()
            await asyncio.wait_for(completed.wait(), timeout=1)

    async def test_operation_cancel_stops_producer_and_marks_run_cancelled(self) -> None:
        handlers: dict[str, object] = {}
        cancelled = asyncio.Event()

        async def source(_db):
            yield (
                'data: {"type":"run","run":{"id":"run-1",'
                '"operation_id":"operation-1"}}\n\n'
            )
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def heartbeat(_operation_id):
            await asyncio.Event().wait()

        def register(_operation_id, **actions):
            handlers.update(actions)

        mark_cancelled = Mock()
        with (
            patch(
                "app.services.workspace.assistant_stream_runtime.SessionLocal",
                return_value=_DummySession(),
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime.register_operation_actions",
                side_effect=register,
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime.unregister_operation_actions",
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime.heartbeat_loop",
                side_effect=heartbeat,
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime._mark_cancelled",
                mark_cancelled,
            ),
        ):
            stream = detached_assistant_stream(source)
            first = await anext(stream)
            self.assertIn("operation-1", first)
            self.assertIn("cancel", handlers)
            handlers["cancel"]()
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            mark_cancelled.assert_called_once_with("run-1")

    async def test_known_plan_operation_is_cancellable_before_first_event(self) -> None:
        handlers: dict[str, object] = {}
        source_started = asyncio.Event()
        cancelled = asyncio.Event()

        async def source(_db):
            source_started.set()
            try:
                await asyncio.Event().wait()
                yield "data: [DONE]\n\n"
            finally:
                cancelled.set()

        async def heartbeat(_operation_id):
            await asyncio.Event().wait()

        def register(operation_id, **actions):
            self.assertEqual(operation_id, "operation-plan")
            handlers.update(actions)

        with (
            patch(
                "app.services.workspace.assistant_stream_runtime.SessionLocal",
                return_value=_DummySession(),
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime.register_operation_actions",
                side_effect=register,
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime.unregister_operation_actions",
            ),
            patch(
                "app.services.workspace.assistant_stream_runtime.heartbeat_loop",
                side_effect=heartbeat,
            ),
            patch("app.services.workspace.assistant_stream_runtime._mark_cancelled") as mark_cancelled,
        ):
            stream = detached_assistant_stream(
                source,
                operation_id_hint="operation-plan",
                run_id_hint="run-plan",
            )
            next_chunk = asyncio.create_task(anext(stream))
            await asyncio.wait_for(source_started.wait(), timeout=1)
            self.assertIn("cancel", handlers)
            handlers["cancel"]()
            with self.assertRaises(StopAsyncIteration):
                await next_chunk
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            mark_cancelled.assert_called_once_with("run-plan")


if __name__ == "__main__":
    unittest.main()
