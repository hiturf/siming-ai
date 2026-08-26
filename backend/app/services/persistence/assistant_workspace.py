"""SQLAlchemy adapter for workspace-assistant persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import (
    AssistantConversation,
    AssistantMemory,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    Chapter,
)


class SqlAlchemyAssistantWorkspace:
    def __init__(self, session: Session) -> None:
        self.db = session

    def conversation(self, project_id: str, conversation_id: str):
        return self.db.query(AssistantConversation).filter(
            AssistantConversation.id == conversation_id,
            AssistantConversation.project_id == project_id,
        ).first()

    def conversation_by_canonical(self, project_id: str, canonical_conversation_id: str):
        return self.db.query(AssistantConversation).filter(
            AssistantConversation.canonical_conversation_id == canonical_conversation_id,
            AssistantConversation.project_id == project_id,
        ).first()

    def create_conversation(self, **values: Any):
        conversation = AssistantConversation(**values)
        self.db.add(conversation)
        return conversation

    def create_message(self, **values: Any):
        message = AssistantMessage(**values)
        self.db.add(message)
        return message

    def message(self, message_id: str):
        return self.db.query(AssistantMessage).filter(AssistantMessage.id == message_id).first()

    def conversation_messages(self, conversation_id: str):
        return self.db.query(AssistantMessage).filter(
            AssistantMessage.conversation_id == conversation_id
        ).order_by(
            AssistantMessage.created_at.asc(),
            AssistantMessage.role.desc(),
            AssistantMessage.updated_at.asc(),
            AssistantMessage.id.asc(),
        ).all()

    def previous_assistant_messages(self, conversation_id: str):
        return self.db.query(AssistantMessage).filter(
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.role == "assistant",
            AssistantMessage.status.in_({"completed", "running"}),
        ).order_by(AssistantMessage.created_at.desc()).all()

    def conversations_with_counts(self, project_id: str, scope: str):
        conversations = self.db.query(AssistantConversation).filter(
            AssistantConversation.project_id == project_id,
            AssistantConversation.scope == scope,
        ).order_by(
            AssistantConversation.updated_at.desc(),
            AssistantConversation.created_at.desc(),
        ).all()
        return [
            (
                conversation,
                self.db.query(AssistantMessage).filter(
                    AssistantMessage.conversation_id == conversation.id
                ).count(),
            )
            for conversation in conversations
        ]

    def delete(self, value: Any) -> None:
        self.db.delete(value)

    def runs(self, project_id: str, conversation_id: str | None, *, limit: int):
        query = self.db.query(AssistantRun).filter(AssistantRun.project_id == project_id)
        if conversation_id:
            query = query.filter(AssistantRun.conversation_id == conversation_id)
        return query.order_by(AssistantRun.created_at.desc()).limit(limit).all()

    def run(self, project_id: str, run_id: str):
        return self.db.query(AssistantRun).filter(
            AssistantRun.project_id == project_id,
            AssistantRun.id == run_id,
        ).first()

    def run_steps(self, run_id: str):
        return self.db.query(AssistantRunStep).filter(
            AssistantRunStep.run_id == run_id
        ).order_by(
            AssistantRunStep.created_at.asc(),
            AssistantRunStep.id.asc(),
        ).all()

    def chapter(self, project_id: str, chapter_id: str):
        return self.db.query(Chapter).filter(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
        ).first()

    def memories(self, project_id: str, categories: Sequence[str], *, limit: int):
        return self.db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(categories),
        ).order_by(
            AssistantMemory.importance.desc(),
            AssistantMemory.updated_at.desc(),
        ).limit(limit).all()

    def related_memories(
        self,
        project_id: str,
        categories: Sequence[str],
        terms: Sequence[str],
        *,
        limit: int,
    ):
        query = self.db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(categories),
        )
        for term in terms:
            query = query.filter(
                AssistantMemory.key.ilike(f"%{term}%")
                | AssistantMemory.value.ilike(f"%{term}%")
            )
        return query.order_by(AssistantMemory.importance.desc()).limit(limit).all()


__all__ = ["SqlAlchemyAssistantWorkspace"]
