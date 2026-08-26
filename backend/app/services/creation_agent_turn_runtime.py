"""Detached, reconnectable SSE runtime for conversational creation turns."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.database.session import SessionLocal
from app.modules.assistant.application.system_conversations import SystemConversationStore
from app.modules.assistant.infrastructure.system_conversations import (
    SqlAlchemySystemConversationStore,
)
from app.modules.creation.interfaces.session_dependencies import novel_creation_session_store
from app.services.novel_creation_agent import (
    creation_agent_replay_messages,
    run_creation_agent,
)
from app.services.observability.run_events import classify_failure

TurnPublisher = Callable[[dict[str, Any]], Awaitable[None]]
TurnProducer = Callable[[TurnPublisher], Awaitable[None]]

_TURN_RETENTION_SECONDS = 15 * 60
_HEARTBEAT_SECONDS = 10


class CreationTurnScopeError(RuntimeError):
    """A requested conversation is not owned by the creation session."""


@dataclass(frozen=True)
class CreationAgentTurnInput:
    session_id: str
    message: str
    client_turn_id: str
    model: str | None
    conversation_id: str | None
    assistant_message_id: str | None
    local_cli_read_paths: tuple[str, ...]
    request_provider: Any = None


@dataclass
class _TurnContext:
    request: CreationAgentTurnInput
    db: Session
    conversations: SystemConversationStore
    conversation_id: str | None
    assistant_message_id: str | None
    conversation_detail: dict[str, Any] | None = None
    audit_events: list[dict[str, Any]] = field(default_factory=list)


def creation_agent_conversation(
    conversations: SystemConversationStore,
    *,
    session_id: str,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    detail: dict[str, Any] | None = None
    if conversation_id:
        detail = conversations.get(conversation_id)
    else:
        listed = conversations.list(scope_type="creation", scope_id=session_id)
        items = listed.get("items") if isinstance(listed, dict) else []
        if isinstance(items, list) and items:
            candidate_id = str((items[0] or {}).get("id") or "").strip()
            if candidate_id:
                detail = conversations.get(candidate_id)
    if detail is None:
        return None
    conversation = detail.get("conversation") if isinstance(detail, dict) else None
    if (
        str((conversation or {}).get("scope_type") or "") != "creation"
        or str((conversation or {}).get("scope_id") or "") != session_id
    ):
        raise CreationTurnScopeError("系统助手对话不属于当前立项会话")
    return detail


def _persist_creation_agent_turn(
    context: _TurnContext,
    *,
    assistant_content: str,
    status: str,
    trace: dict[str, Any],
    run: dict[str, Any] | None,
    result: dict[str, Any],
) -> None:
    if not context.conversation_id or not context.assistant_message_id:
        raise CreationTurnScopeError("立项助手消息尚未绑定到系统对话")
    messages = (context.conversation_detail or {}).get("messages") or []
    if not any(
        isinstance(item, dict)
        and item.get("id") == context.assistant_message_id
        and item.get("role") == "assistant"
        for item in messages
    ):
        raise CreationTurnScopeError("立项助手消息不属于当前系统对话")
    payload: dict[str, Any] = {
        "creation_agent_turn": trace,
        "creation_agent_result": result,
    }
    if run:
        payload["run"] = run
    context.conversations.finish_turn(
        context.conversation_id,
        context.assistant_message_id,
        {
            "assistant_content": assistant_content,
            "status": status,
            "creation_session_id": context.request.session_id,
            "scope_type": "creation",
            "scope_id": context.request.session_id,
            "run_id": (run or {}).get("id") or (run or {}).get("run_id"),
            "operation_id": (run or {}).get("operation_id"),
            "message_type": "operation" if run else "text",
            "payload": payload,
        },
    )


def safe_creation_agent_error(exc: Exception) -> tuple[str, dict[str, Any]]:
    failure_class = classify_failure(str(exc)) or "unknown"
    message, next_action = {
        "quota_or_rate_limit": (
            "模型额度已耗尽或请求受限",
            "请等待额度恢复，或切换到有额度的模型后重试。",
        ),
        "auth": ("模型授权已失效", "请到模型设置重新登录或填写凭据，测试成功后重试。"),
        "timeout": ("模型响应超时", "后台状态已保留，可稍后重试或切换更快的模型。"),
        "network": ("模型网络连接中断", "请检查网络或本机模型进程后重试。"),
        "empty_response": ("模型没有返回有效内容", "请重试本轮或切换模型。"),
        "invalid_response": ("模型返回格式无法解析", "请重试本轮或切换模型。"),
    }.get(failure_class, ("立项助手处理失败", "请检查模型状态后重试本轮。"))
    return message, {
        "error_type": type(exc).__name__,
        "failure_class": failure_class,
        "next_action": next_action,
    }


async def _emit(
    context: _TurnContext,
    publish: TurnPublisher,
    event: dict[str, Any],
) -> None:
    safe_event = {
        "type": str(event.get("type") or ""),
        "message": str(event.get("message") or "")[:500],
        "data": dict(event.get("data") or {}),
    }
    context.audit_events.append(safe_event)
    await publish(safe_event)


async def _recover_existing_turn(
    context: _TurnContext,
    publish: TurnPublisher,
) -> bool:
    interrupted_message: dict[str, Any] | None = None
    for stored_message in reversed((context.conversation_detail or {}).get("messages") or []):
        if not isinstance(stored_message, dict) or stored_message.get("role") != "assistant":
            continue
        stored_payload = stored_message.get("payload")
        trace = stored_payload.get("creation_agent_turn") if isinstance(stored_payload, dict) else None
        if isinstance(trace, dict) and trace.get("client_turn_id") == context.request.client_turn_id:
            stored_result = stored_payload.get("creation_agent_result")
            if isinstance(stored_result, dict):
                await _emit(context, publish, {
                    "type": "complete",
                    "message": "已恢复本轮最终结果",
                    "data": stored_result,
                })
                return True
        marker = stored_payload.get("creation_agent_client_turn_id") if isinstance(stored_payload, dict) else None
        if marker == context.request.client_turn_id and interrupted_message is None:
            interrupted_message = stored_message
    if interrupted_message is None:
        return False
    recovery_message = (
        "上次服务进程在本轮完成前中断；为避免重复写入，本次不会重新执行。"
        "请检查已保存结果后发送一条新消息重试。"
    )
    recovery_data = {
        "error_type": "turn_recovery_required",
        "failure_class": "interrupted",
        "next_action": "检查当前 revision 和已保存消息，然后使用新的请求重试。",
    }
    conversation = (context.conversation_detail or {}).get("conversation") or {}
    conversation_id = str(conversation.get("id") or "")
    assistant_id = str(interrupted_message.get("id") or "")
    if conversation_id and assistant_id:
        context.conversations.finish_turn(conversation_id, assistant_id, {
            "assistant_content": recovery_message,
            "status": "error",
            "creation_session_id": context.request.session_id,
            "scope_type": "creation",
            "scope_id": context.request.session_id,
            "payload": {
                "creation_agent_client_turn_id": context.request.client_turn_id,
                "creation_agent_error": recovery_data,
            },
        })
        commit_session(context.db)
    await _emit(context, publish, {
        "type": "error",
        "message": recovery_message,
        "data": recovery_data,
    })
    return True


def _bind_pending_turn(context: _TurnContext) -> None:
    request = context.request
    if context.assistant_message_id and context.conversation_id:
        messages = (context.conversation_detail or {}).get("messages") or []
        if not any(
            isinstance(item, dict)
            and item.get("id") == context.assistant_message_id
            and item.get("role") == "assistant"
            for item in messages
        ):
            raise CreationTurnScopeError("立项助手消息不属于当前系统对话")
    else:
        if not context.conversation_id:
            context.conversation_id = str(
                ((context.conversation_detail or {}).get("conversation") or {}).get("id") or ""
            ).strip()
        if not context.conversation_id:
            created = context.conversations.create(
                request.message[:36],
                scope_type="creation",
                scope_id=request.session_id,
            )
            context.conversation_id = str((created.get("conversation") or {}).get("id") or "")
        started = context.conversations.start_turn(context.conversation_id, {
            "user_content": request.message,
            "creation_session_id": request.session_id,
            "scope_type": "creation",
            "scope_id": request.session_id,
            "payload": {"creation_agent_client_turn_id": request.client_turn_id},
        })
        messages = started.get("messages") if isinstance(started, dict) else []
        context.assistant_message_id = str((messages or [{}, {}])[-1].get("id") or "")
        commit_session(context.db)
        context.conversation_detail = context.conversations.get(context.conversation_id)
    context.conversations.finish_turn(
        context.conversation_id,
        context.assistant_message_id,
        {
            "assistant_content": "",
            "status": "running",
            "creation_session_id": request.session_id,
            "scope_type": "creation",
            "scope_id": request.session_id,
            "payload": {"creation_agent_client_turn_id": request.client_turn_id},
        },
    )
    commit_session(context.db)


async def _execute_agent(
    context: _TurnContext,
    source_session: Any,
    publish: TurnPublisher,
) -> None:
    request = context.request
    await _emit(context, publish, {
        "type": "turn_started",
        "message": "立项对话已绑定，正在调用模型…",
        "data": {
            "session_id": request.session_id,
            "conversation_id": context.conversation_id,
            "assistant_message_id": context.assistant_message_id,
        },
    })
    replay_messages = creation_agent_replay_messages(
        context.conversation_detail,
        session_id=request.session_id,
        exclude_assistant_message_id=context.assistant_message_id,
    )

    async def invoke() -> dict[str, Any]:
        return await run_creation_agent(
            context.db,
            session=source_session,
            message=request.message,
            model=request.model,
            replay_messages=replay_messages,
            local_cli_read_paths=list(request.local_cli_read_paths),
            on_event=lambda event: _emit(context, publish, event),
        )

    if request.request_provider is None:
        result = await invoke()
    else:
        from app.modules.model_runtime.application.request_override import use_request_provider

        with use_request_provider(request.request_provider):
            result = await invoke()
    trace = result.pop("_turn_trace")
    trace["client_turn_id"] = request.client_turn_id
    trace["progress_events"] = [
        *context.audit_events,
        {"type": "complete", "message": "本轮立项处理完成", "data": {}},
    ]
    run = result.get("run") if isinstance(result.get("run"), dict) else None
    outcome = trace.get("outcome") if isinstance(trace, dict) else None
    message_status = (
        "error"
        if isinstance(outcome, dict) and outcome.get("status") == "protocol_error"
        else "running"
        if run and run.get("status") in {"queued", "running"}
        else "completed"
    )
    result.update({
        "message_status": message_status,
        "conversation_id": context.conversation_id,
        "assistant_message_id": context.assistant_message_id,
        "turn_persisted": True,
    })
    _persist_creation_agent_turn(
        context,
        assistant_content=str(result.get("reply") or ""),
        status=message_status,
        trace=trace,
        run=run,
        result=result,
    )
    commit_session(context.db)
    await publish({
        "type": "complete",
        "message": "本轮立项处理完成",
        "data": result,
    })


async def _persist_turn_error(
    context: _TurnContext,
    publish: TurnPublisher,
    exc: Exception,
) -> None:
    context.db.rollback()
    safe_message, safe_error_data = safe_creation_agent_error(exc)
    if context.conversation_id and context.assistant_message_id:
        try:
            context.conversations.finish_turn(
                context.conversation_id,
                context.assistant_message_id,
                {
                    "assistant_content": safe_message,
                    "status": "error",
                    "creation_session_id": context.request.session_id,
                    "scope_type": "creation",
                    "scope_id": context.request.session_id,
                    "payload": {
                        "creation_agent_client_turn_id": context.request.client_turn_id,
                        "creation_agent_error": {
                            "type": type(exc).__name__,
                            "message": safe_message,
                            **safe_error_data,
                            "client_turn_id": context.request.client_turn_id,
                            "replayable": False,
                        },
                        "creation_agent_progress": [
                            *context.audit_events,
                            {"type": "error", "message": safe_message, "data": safe_error_data},
                        ],
                    },
                },
            )
            commit_session(context.db)
        except Exception:
            context.db.rollback()
    await publish({"type": "error", "message": safe_message, "data": safe_error_data})


async def produce_creation_agent_turn(
    request: CreationAgentTurnInput,
    publish: TurnPublisher,
) -> None:
    source_db = SessionLocal()
    context = _TurnContext(
        request=request,
        db=source_db,
        conversations=SqlAlchemySystemConversationStore(source_db),
        conversation_id=request.conversation_id,
        assistant_message_id=request.assistant_message_id,
    )
    await _emit(context, publish, {
        "type": "turn_started",
        "message": "已接收请求，正在准备立项上下文…",
        "data": {"session_id": request.session_id},
    })
    try:
        source_session = novel_creation_session_store(source_db).session(request.session_id)
        if not source_session:
            raise RuntimeError("立项草稿不存在")
        context.conversation_detail = creation_agent_conversation(
            context.conversations,
            session_id=request.session_id,
            conversation_id=context.conversation_id,
        )
        if await _recover_existing_turn(context, publish):
            return
        _bind_pending_turn(context)
        await _execute_agent(context, source_session, publish)
    except Exception as exc:
        await _persist_turn_error(context, publish, exc)
    finally:
        source_db.close()


@dataclass
class _CreationTurnExecution:
    client_turn_id: str
    request_fingerprint: str
    sequence_base: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[Any] | None = None
    done: bool = False
    last_publish_at: float = field(default_factory=time.monotonic)
    started_at: float = field(default_factory=time.monotonic)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self.condition:
            payload = {
                **event,
                "client_turn_id": self.client_turn_id,
                "sequence": self.sequence_base + len(self.events) + 1,
            }
            self.events.append(payload)
            self.last_publish_at = time.monotonic()
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.done = True
            self.condition.notify_all()


_EXECUTIONS: dict[str, _CreationTurnExecution] = {}
_EXECUTIONS_LOCK = asyncio.Lock()


async def _run_execution(execution: _CreationTurnExecution, producer: TurnProducer) -> None:
    heartbeat_task: asyncio.Task[Any] | None = None

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            if execution.done:
                return
            if time.monotonic() - execution.last_publish_at < _HEARTBEAT_SECONDS:
                continue
            elapsed = int(time.monotonic() - execution.started_at)
            await execution.publish({
                "type": "heartbeat",
                "message": f"模型仍在响应，已等待 {elapsed} 秒",
                "data": {"elapsed_seconds": elapsed},
            })

    try:
        heartbeat_task = asyncio.create_task(
            heartbeat(),
            name=f"creation-turn-heartbeat-{execution.client_turn_id}",
        )
        await producer(execution.publish)
    except asyncio.CancelledError:
        await execution.publish({
            "type": "cancelled",
            "message": "本轮处理已取消",
            "data": {},
        })
        raise
    except Exception as exc:
        if not execution.events or execution.events[-1].get("type") not in {"error", "complete"}:
            await execution.publish({
                "type": "error",
                "message": "立项助手执行中断，请稍后重试",
                "data": {"error_type": type(exc).__name__},
            })
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        await execution.finish()
        asyncio.create_task(
            _expire_execution(execution.client_turn_id, execution),
            name=f"creation-turn-expire-{execution.client_turn_id}",
        )


async def _expire_execution(client_turn_id: str, execution: _CreationTurnExecution) -> None:
    await asyncio.sleep(_TURN_RETENTION_SECONDS)
    async with _EXECUTIONS_LOCK:
        if _EXECUTIONS.get(client_turn_id) is execution:
            _EXECUTIONS.pop(client_turn_id, None)


async def creation_agent_turn_stream(
    *,
    client_turn_id: str,
    request_fingerprint: str,
    after_sequence: int,
    producer: TurnProducer,
) -> AsyncIterator[str]:
    """Start or reattach to one idempotent creation turn."""

    conflict_payload: dict[str, Any] | None = None
    async with _EXECUTIONS_LOCK:
        execution = _EXECUTIONS.get(client_turn_id)
        if execution is not None and execution.request_fingerprint != request_fingerprint:
            sequence = max(int(after_sequence or 0), 0) + 1
            conflict_payload = {
                "client_turn_id": client_turn_id,
                "sequence": sequence,
                "type": "error",
                "message": "client_turn_id 已绑定到另一条立项消息",
                "data": {"error_type": "client_turn_conflict"},
            }
        elif execution is None:
            execution = _CreationTurnExecution(
                client_turn_id,
                request_fingerprint,
                sequence_base=max(int(after_sequence or 0), 0),
            )
            _EXECUTIONS[client_turn_id] = execution
            execution.task = asyncio.create_task(
                _run_execution(execution, producer),
                name=f"creation-agent-turn-{client_turn_id}",
            )

    if conflict_payload is not None:
        sequence = int(conflict_payload["sequence"])
        yield f"id: {sequence}\ndata: {json.dumps(conflict_payload, ensure_ascii=False)}\n\n"
        return
    assert execution is not None

    sent = max(int(after_sequence or 0), 0)
    while True:
        async with execution.condition:
            pending = [
                event
                for event in execution.events
                if int(event.get("sequence") or 0) > sent
            ]
            if not pending and not execution.done:
                await execution.condition.wait()
                continue
            done = execution.done
        for event in pending:
            sequence = int(event.get("sequence") or 0)
            sent = max(sent, sequence)
            yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        if done and not any(int(event.get("sequence") or 0) > sent for event in execution.events):
            break


__all__ = ["creation_agent_turn_stream"]
