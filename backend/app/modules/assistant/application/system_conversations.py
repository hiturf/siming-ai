"""System-assistant conversation application port."""

from __future__ import annotations

from typing import Any, Protocol


class SystemConversationStore(Protocol):
    def list(
        self, *, scope_type: str | None = None, scope_id: str | None = None
    ) -> dict[str, Any]: ...

    def create(
        self, title: str, *, scope_type: str, scope_id: str
    ) -> dict[str, Any]: ...

    def set_scope(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, conversation_id: str) -> dict[str, Any]: ...

    def start_turn(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def finish_turn(
        self,
        conversation_id: str,
        assistant_message_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    def interrupt_running_messages(self) -> int: ...

    def append_turn(
        self,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    def delete(self, conversation_id: str) -> dict[str, Any]: ...


__all__ = ["SystemConversationStore"]
