"""Pydantic schemas for AI writing engine endpoints."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class MobileProviderEnvelope(BaseModel):
    """End-to-end encrypted Android-owned provider configuration."""

    version: Literal[1] = 1
    ephemeral_public_key: str = Field(min_length=40, max_length=64)
    nonce: str = Field(min_length=16, max_length=32)
    ciphertext: str = Field(min_length=32, max_length=100_000)


class WorkspaceAssistantRequest(BaseModel):
    """Conversational assistant for a project workspace."""

    message: str = Field(..., min_length=1, max_length=1_000_000)
    conversation_id: Optional[str] = None
    canonical_conversation_id: Optional[str] = Field(
        None,
        description="Canonical project conversation ID used to reuse the internal execution thread",
    )
    selected_text: Optional[str] = Field(None, description="User-selected text in the editor")
    selected_text_chapter_id: Optional[str] = Field(None, description="Chapter ID the selected text belongs to")
    model: Optional[str] = None
    temperature: Optional[float] = Field(0.3, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    outline_batch_count: int = Field(3, ge=1, le=12, description="Preferred number of consecutive outline chapters to plan")
    local_cli_read_permission_grant: Literal["none", "read_once"] = Field(
        "none",
        description="One-turn consent to snapshot explicitly named local paths for OpenCode",
    )
    local_cli_read_paths: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Absolute files/directories explicitly confirmed by the user for this turn",
    )
    history: list[dict] = Field(default_factory=list)
    model_route: Literal["pc", "mobile"] = "pc"
    mobile_provider: Optional[MobileProviderEnvelope] = Field(
        None,
        repr=False,
        exclude=True,
        description="Encrypted, request-only provider credentials from a paired Android device",
    )

    @model_validator(mode="after")
    def require_mobile_provider_envelope(self):
        if self.model_route == "mobile" and self.mobile_provider is None:
            raise ValueError("选择手机模型线路时必须提供加密凭据")
        if self.model_route == "pc" and self.mobile_provider is not None:
            raise ValueError("PC 模型线路不能携带手机模型凭据")
        return self


class WorkspaceAssistantRunResponse(BaseModel):
    """Stable public contract for one durable workspace-assistant run."""

    run_id: str
    operation_id: Optional[str] = None
    actual_model: Optional[str] = None
    status: str

    # Compatibility aliases retained for pre-3.1 clients.
    id: str
    model: Optional[str] = None

    project_id: str
    conversation_id: Optional[str] = None
    canonical_conversation_id: Optional[str] = Field(
        None,
        description="Canonical project conversation ID used to reuse the internal execution thread",
    )
    assistant_message_id: Optional[str] = None
    phase: Optional[str] = None
    scope: Optional[str] = None
    current_iteration: int = 0
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class WorkspaceAssistantRunStepResponse(BaseModel):
    id: str
    run_id: str
    step_type: str
    tool: Optional[str] = None
    status: str
    iteration: int = 0
    detail: Optional[str] = None
    error: Optional[str] = None
    attempt_no: int = 1
    retry_of_step_id: Optional[str] = None
    resolved_step_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    can_retry: bool = False
    retry_block_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    request: Any = None
    result: Any = None


class WorkspaceAssistantRunListResponse(BaseModel):
    items: list[WorkspaceAssistantRunResponse]
    total: int


class WorkspaceAssistantRunDetailResponse(BaseModel):
    run: WorkspaceAssistantRunResponse
    assistant_message: Optional[dict[str, Any]] = None
    steps: list[WorkspaceAssistantRunStepResponse]
