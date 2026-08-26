"""Public contract tests for durable workspace-assistant runs."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.main import app
from app.schemas.ai_writer import WorkspaceAssistantRunResponse
from app.services.workspace.run_log import run_payload


def _run() -> SimpleNamespace:
    now = datetime.utcnow()
    return SimpleNamespace(
        id="run-1",
        project_id="project-1",
        conversation_id="conversation-1",
        assistant_message_id="message-1",
        operation_id="operation-1",
        status="running",
        phase="model",
        scope="project",
        model="openai:gpt-test",
        current_iteration=2,
        error=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


def test_run_payload_exposes_stable_names_and_compatibility_aliases() -> None:
    payload = run_payload(_run())

    assert payload["run_id"] == payload["id"] == "run-1"
    assert payload["operation_id"] == "operation-1"
    assert payload["actual_model"] == payload["model"] == "openai:gpt-test"
    assert payload["status"] == "running"
    assert payload["created_at"].endswith("+00:00")
    assert payload["updated_at"].endswith("+00:00")
    validated = WorkspaceAssistantRunResponse.model_validate(payload)
    assert validated.run_id == "run-1"


def test_run_queries_publish_typed_openapi_responses() -> None:
    schema = app.openapi()
    list_response = schema["paths"]["/api/v1/projects/{project_id}/ai/assistant/runs"]["get"]["responses"]["200"]
    detail_response = schema["paths"]["/api/v1/projects/{project_id}/ai/assistant/runs/{run_id}"]["get"]["responses"]["200"]

    list_ref = list_response["content"]["application/json"]["schema"]["$ref"]
    detail_ref = detail_response["content"]["application/json"]["schema"]["$ref"]
    assert "WorkspaceAssistantRunListResponse" in list_ref
    assert "WorkspaceAssistantRunDetailResponse" in detail_ref

    run_schema = schema["components"]["schemas"]["WorkspaceAssistantRunResponse"]
    assert {"run_id", "operation_id", "actual_model", "status", "id", "model"}.issubset(
        run_schema["properties"]
    )

