"""SQLAlchemy persistence models owned by the operations module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.models_support import generate_uuid
from app.database.session import Base


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    prompt = Column(Text, nullable=False)
    cron_expr = Column(String(100), nullable=True)
    interval_minutes = Column(Integer, nullable=True)
    tool_policy = Column(JSON, nullable=True, default=list)
    status = Column(String(20), nullable=False, default="active")
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String(20), nullable=True)
    last_run_output = Column(Text, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="scheduled_tasks")

    __table_args__ = (
        Index("ix_scheduled_tasks_project_status", "project_id", "status"),
        Index("ix_scheduled_tasks_next_run", "next_run_at"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(50), nullable=False, default="mcp")  # mcp | internal
    client_name = Column(String(100), nullable=True)  # claude-code, codex, etc.
    title = Column(String(200), nullable=True)
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(
        String(30), nullable=False, default="created"
    )  # created|running|waiting_confirmation|completed|failed|cancelled
    current_step = Column(String(200), nullable=True)
    summary = Column(Text, nullable=True)
    context_manifest_id = Column(
        String(36), ForeignKey("context_manifests.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="agent_runs")
    events = relationship(
        "AgentRunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentRunEvent.sequence",
    )

    __table_args__ = (
        Index("ix_agent_runs_project_status", "project_id", "status"),
        Index("ix_agent_runs_created", "created_at"),
    )


class AgentRunEvent(Base):
    __tablename__ = "agent_run_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(30), nullable=False)  # plan|progress|tool_start|tool_result|...
    status = Column(String(20), nullable=False, default="ok")  # ok|running|error|skipped
    message = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)  # JSON string, max 10000 chars
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    run = relationship("AgentRun", back_populates="events")

    __table_args__ = (
        Index("ix_agent_run_events_run_seq", "run_id", "sequence", unique=True),
        Index("ix_agent_run_events_created", "created_at"),
    )


__all__ = [
    "ScheduledTask",
    "AgentRun",
    "AgentRunEvent",
]
