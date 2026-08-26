"""Persisted system-level assistant conversations."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from ..core.response import ApiResponse
from ..modules.assistant.application.system_conversations import SystemConversationStore
from ..modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)

router = APIRouter(tags=["assistant-conversations"])


class _ScopedPayload(BaseModel):
    @model_validator(mode="after")
    def validate_scope_pair(self):
        scope_type = getattr(self, "scope_type", None)
        scope_id = getattr(self, "scope_id", None)
        if scope_type in {"creation", "project"} and not str(scope_id or "").strip():
            raise ValueError(f"{scope_type} scope requires scope_id")
        return self


class SystemConversationCreate(_ScopedPayload):
    title: str = ""
    scope_type: Literal["creation", "project"]
    scope_id: str | None = None


class SystemConversationScopePatch(_ScopedPayload):
    scope_type: Literal["creation", "project"]
    scope_id: str | None = None


class SystemTurnCreate(_ScopedPayload):
    user_content: str = Field(min_length=1, max_length=1_000_000)
    assistant_content: str = ""
    status: str = "completed"
    payload: dict[str, Any] | None = None
    creation_session_id: str | None = None
    user_brief: str | None = None
    run_id: str | None = None
    operation_id: str | None = None
    message_type: str = "text"
    scope_type: Literal["creation", "project"] | None = None
    scope_id: str | None = None
    project_id: str | None = None


class SystemTurnFinish(_ScopedPayload):
    assistant_content: str = ""
    status: str = "completed"
    payload: dict[str, Any] | None = None
    creation_session_id: str | None = None
    user_brief: str | None = None
    run_id: str | None = None
    operation_id: str | None = None
    message_type: str | None = None
    scope_type: Literal["creation", "project"] | None = None
    scope_id: str | None = None
    project_id: str | None = None


@router.get("/ai/assistant/conversations")
async def list_system_conversations(
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
    scope_type: Literal["creation", "project"] | None = None,
    scope_id: str | None = None,
):
    data = await run_in_threadpool(
        conversations.list,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    return ApiResponse.success(data=data)


@router.post("/ai/assistant/conversations")
async def create_system_conversation(
    payload: SystemConversationCreate,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    data = await run_in_threadpool(
        conversations.create,
        payload.title,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
    )
    return ApiResponse.success(data=data)


@router.patch("/ai/assistant/conversations/{conversation_id}/scope")
async def set_system_conversation_scope(
    conversation_id: str,
    payload: SystemConversationScopePatch,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    data = await run_in_threadpool(
        conversations.set_scope,
        conversation_id,
        payload.model_dump(),
    )
    return ApiResponse.success(data=data)


@router.get("/ai/assistant/conversations/{conversation_id}")
async def get_system_conversation(
    conversation_id: str,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(
        data=await run_in_threadpool(conversations.get, conversation_id)
    )


@router.post("/ai/assistant/conversations/{conversation_id}/turns/start")
async def start_system_turn(
    conversation_id: str,
    payload: SystemTurnCreate,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    data = await run_in_threadpool(
        conversations.start_turn,
        conversation_id,
        payload.model_dump(),
    )
    return ApiResponse.success(data=data)


@router.patch("/ai/assistant/conversations/{conversation_id}/turns/{assistant_message_id}")
async def finish_system_turn(
    conversation_id: str,
    assistant_message_id: str,
    payload: SystemTurnFinish,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    data = await run_in_threadpool(
        conversations.finish_turn,
        conversation_id,
        assistant_message_id,
        payload.model_dump(),
    )
    return ApiResponse.success(data=data)


@router.post("/ai/assistant/conversations/{conversation_id}/turns")
async def append_system_turn(
    conversation_id: str,
    payload: SystemTurnCreate,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    data = await run_in_threadpool(
        conversations.append_turn,
        conversation_id,
        payload.model_dump(),
    )
    return ApiResponse.success(data=data)


@router.delete("/ai/assistant/conversations/{conversation_id}")
async def delete_system_conversation(
    conversation_id: str,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(
        data=await run_in_threadpool(conversations.delete, conversation_id)
    )


__all__ = [
    "SystemConversationCreate",
    "SystemTurnCreate",
    "append_system_turn",
    "create_system_conversation",
    "delete_system_conversation",
    "get_system_conversation",
    "list_system_conversations",
    "router",
]
