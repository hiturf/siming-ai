"""Pydantic schemas for novel creation sessions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any, Literal

from pydantic import BaseModel, Field


class NovelCreationSessionCreate(BaseModel):
    """Schema for creating a novel creation session."""
    mode: str = Field(default="internal_llm", description="internal_llm|external_agent")
    user_brief: Optional[str] = Field(default=None, description="User's novel brief")
    target_audience: Optional[str] = None
    genre: Optional[str] = None
    platform: Optional[str] = None
    preset_id: Optional[str] = None
    theme_id: Optional[str] = None
    target_words: Optional[int] = None
    target_chapters: Optional[int] = None
    creation_mode: str = Field(default="explore", description="author_led|explore")
    author_brief: Optional[str] = Field(default=None, max_length=5000)
    author_outline: Optional[str] = Field(default=None, max_length=20000)
    locked_requirements: list[str] = Field(default_factory=list)


class NovelCreationSessionRead(BaseModel):
    """Schema for reading a novel creation session."""
    id: str
    source_project_id: Optional[str] = None
    created_project_id: Optional[str] = None
    status: str
    mode: str
    user_brief: Optional[str] = None
    target_audience: Optional[str] = None
    genre: Optional[str] = None
    platform: Optional[str] = None
    schema_version: int = 1
    creation_mode: str = "explore"
    author_brief: Optional[str] = None
    author_outline: Optional[str] = None
    locked_requirements: list[str] = Field(default_factory=list)
    current_stage: Optional[str] = None
    revision: int = 0
    review_json: Optional[Any] = None
    draft_json: Optional[Any] = None
    checkpoints_json: Optional[Any] = None
    last_error_json: Optional[Any] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NovelCreationStageEventResponse(BaseModel):
    sequence: int
    event_type: str
    status: str
    message: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None


class NovelCreationRunCardPresentation(BaseModel):
    """Author-facing interpretation of a durable run's factual evidence."""

    status: Literal[
        "queued", "running", "waiting_user", "paused", "completed",
        "partial_success", "failed", "cancelled", "interrupted",
    ]
    label: str
    message: str
    show_retry: bool = False
    judged_by: Literal["model", "fallback"] = "model"
    reason: Optional[str] = None
    raw_status: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    resolved_model: Optional[str] = None
    route: Optional[Literal["api", "cli", "unknown"]] = None


class NovelCreationModelStreamProgress(BaseModel):
    kind: Literal["model_output"] = "model_output"
    output_chars: int = Field(ge=0)
    output_preview: Optional[str] = None
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    attempt: Optional[int] = Field(default=None, ge=1)


class NovelCreationStageRunResponse(BaseModel):
    """Stable wire contract for one durable novel-creation stage run."""

    run_id: str
    operation_id: Optional[str] = None
    status: str

    # Compatibility alias retained for clients that predate the durable-run API.
    id: str
    session_id: str
    stage: str
    operation: str
    model_source: Optional[str] = None
    tool_mode: Optional[str] = None
    failure_class: Optional[str] = None
    storage_target: str
    context_manifest_id: Optional[str] = None
    input_revision: Optional[int] = None
    input_snapshot_hash: Optional[str] = None
    next_action: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    attempt: int = 0
    result_mode: Optional[str] = None
    warning: Optional[str] = None
    diagnostic_count: int = 0
    stream_progress: Optional[NovelCreationModelStreamProgress] = None
    current_message: Optional[str] = None
    card_presentation: Optional[NovelCreationRunCardPresentation] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    events: list[NovelCreationStageEventResponse] = Field(default_factory=list)


class NovelCreationStageRunStartData(BaseModel):
    run: NovelCreationStageRunResponse
    stream_url: str
    replayed: bool = False
