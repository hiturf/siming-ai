"""Focused contracts for v3.1 durable creation and assistant runs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    NovelCreationRunClaim,
    NovelCreationSession,
    NovelCreationStageRun,
    OperationRun,
)
from app.database.session import Base
from app.main import app
from app.modules.creation.infrastructure.session_store import SqlAlchemyNovelCreationSessionStore
from app.services.novel_creation_claims import (
    claim_or_replay_creation_run,
    creation_idempotency_key,
)
from app.services.novel_creation_runs import (
    create_run,
    complete_run,
    confirm_run,
    mark_interrupted_novel_creation_runs,
    serialize_run,
)
from app.services.novel_creation_stage_execution import _capture_model_diagnostic
from app.services.novel_creation_task_runtime import invoke_durable_creation_action
from app.services.novel_creation_workspace import serialize_session
from app.services.workspace.run_log import resolve_assistant_model
from app.routers.novel_creation import (
    NovelCreationConfirmAndGenerateRequest,
    NovelCreationStageRetryRequest,
    NovelCreationStageConfirmRequest,
    confirm_and_generate_recommended,
    confirm_creation_stage,
    retry_creation_stage_run,
)
from app.services.novel_creation_workspace import initialize_session_draft, save_stage


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_creation_run_openapi_exposes_durable_result_contract() -> None:
    schema = app.openapi()
    start_response = schema["paths"]["/api/v1/novel-creation/sessions/{session_id}/runs"]["post"]["responses"]["200"]
    query_response = schema["paths"]["/api/v1/novel-creation/runs/{run_id}"]["get"]["responses"]["200"]

    assert "NovelCreationStageRunStartData" in start_response["content"]["application/json"]["schema"]["$ref"]
    assert "NovelCreationStageRunResponse" in query_response["content"]["application/json"]["schema"]["$ref"]
    properties = schema["components"]["schemas"]["NovelCreationStageRunResponse"]["properties"]
    assert {
        "run_id",
        "operation_id",
        "status",
        "attempt",
        "result_mode",
        "warning",
        "card_presentation",
    }.issubset(properties)
    paths = schema["paths"]
    assert "post" in paths["/api/v1/novel-creation/runs/{run_id}/card-presentation"]


def test_creation_artifact_openapi_exposes_query_patch_lock_and_confirm_routes() -> None:
    paths = app.openapi()["paths"]
    artifact_path = "/api/v1/novel-creation/sessions/{session_id}/artifacts/{stage}"
    lock_path = artifact_path + "/locks"
    assert "get" in paths[artifact_path]
    assert "patch" in paths[artifact_path]
    assert "get" in paths[artifact_path + "/dependencies"]
    assert "post" in paths[lock_path]
    assert "delete" in paths[lock_path]
    assert "post" in paths[artifact_path + "/undo"]
    assert "post" in paths["/api/v1/novel-creation/sessions/{session_id}/stages/{stage}/confirm"]
    assert "post" in paths["/api/v1/novel-creation/sessions/{session_id}/stages/{stage}/confirm-and-generate-recommended"]

    patch_schema = paths[artifact_path]["patch"]["requestBody"]["content"]["application/json"]["schema"]
    assert "NovelCreationArtifactPatchRequest" in patch_schema["$ref"]


def test_creation_claim_replays_identical_stage_command() -> None:
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", draft_json={"stages": {}})
    db.add(session)
    db.flush()
    key = creation_idempotency_key(
        session_id=session.id,
        stage="characters",
        operation="generate",
        request={"instruction": None, "model": None},
        input_revision=0,
        input_snapshot_hash="snapshot",
    )

    claim, replayed = claim_or_replay_creation_run(
        db,
        session_id=session.id,
        artifact_key="characters",
        idempotency_key=key,
        input_revision=0,
        input_snapshot_hash="snapshot",
    )
    duplicate, duplicate_replayed = claim_or_replay_creation_run(
        db,
        session_id=session.id,
        artifact_key="characters",
        idempotency_key=key,
        input_revision=0,
        input_snapshot_hash="snapshot",
    )

    assert replayed is False
    assert duplicate_replayed is True
    assert duplicate.id == claim.id


def test_database_rejects_two_active_claims_for_one_artifact() -> None:
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", draft_json={"stages": {}})
    db.add(session)
    db.flush()
    db.add_all([
        NovelCreationRunClaim(
            session_id=session.id,
            artifact_key="characters",
            idempotency_key="concurrent-1",
            claim_token="token-1",
            status="running",
            input_revision=0,
            input_snapshot_hash="snapshot-1",
        ),
        NovelCreationRunClaim(
            session_id=session.id,
            artifact_key="characters",
            idempotency_key="concurrent-2",
            claim_token="token-2",
            status="running",
            input_revision=0,
            input_snapshot_hash="snapshot-2",
        ),
    ])

    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize(
    ("use_latest_draft", "expected_revision", "expected_marker", "expected_mode"),
    [
        (False, 2, "original", "original_input"),
        (True, 3, "latest", "latest_draft"),
    ],
)
def test_retry_uses_the_selected_original_or_latest_input_snapshot(
    use_latest_draft: bool,
    expected_revision: int,
    expected_marker: str,
    expected_mode: str,
) -> None:
    db = _db()
    session = NovelCreationSession(
        mode="internal_llm",
        status="drafting",
        revision=2,
        draft_json={"marker": "original", "stages": {}},
    )
    db.add(session)
    db.flush()
    previous = create_run(db, session, "characters", {
        "model": "openai:first",
        "context_manifest_id": "old-small-budget",
    })
    previous.status = "failed"
    session.draft_json = {"marker": "latest", "stages": {}}
    session.revision = 3
    db.commit()

    with patch("app.routers.novel_creation.schedule_creation_stage") as schedule:
        response = asyncio.run(
            retry_creation_stage_run(
                previous.id,
                NovelCreationStageRetryRequest(
                    use_latest_draft=use_latest_draft,
                    model="openai:retry",
                ),
                db,
            )
        )

    run = db.get(NovelCreationStageRun, response.data["run"]["run_id"])
    assert run is not None
    assert run.input_revision == expected_revision
    assert run.request_json["input_snapshot"]["marker"] == expected_marker
    assert run.request_json["retry_mode"] == expected_mode
    assert run.request_json["model"] == "openai:retry"
    assert "context_manifest_id" not in run.request_json
    assert run.context_manifest_id is None
    schedule.assert_called_once()


def test_creation_run_supports_durable_pause_and_checkpoint_resume() -> None:
    db = _db()
    Session = sessionmaker(bind=db.bind)
    session = NovelCreationSession(mode="internal_llm", status="drafting", draft_json={"stages": {}})
    db.add(session)
    db.flush()
    run = create_run(db, session, "characters", {"model": "openai:test"})
    db.commit()

    assert db.get(OperationRun, run.operation_id).can_pause is True
    with patch("app.services.novel_creation_task_runtime.SessionLocal", Session):
        assert asyncio.run(invoke_durable_creation_action(run.operation_id, "pause")) is True
    db.expire_all()
    assert db.get(NovelCreationStageRun, run.id).status == "paused"

    with (
        patch("app.services.novel_creation_task_runtime.SessionLocal", Session),
        patch("app.services.novel_creation_task_runtime.schedule_creation_stage") as schedule,
    ):
        assert asyncio.run(invoke_durable_creation_action(run.operation_id, "continue")) is True
    db.expire_all()
    resumed = db.get(NovelCreationStageRun, run.id)
    assert resumed.status == "running"
    assert resumed.events[-1].event_type == "continued"
    assert schedule.call_args.args[:2] == (run.id, session.id)
    assert schedule.call_args.args[2]["_resume"] is True


def test_repaired_model_reply_is_kept_in_full_diagnostics_only() -> None:
    run = SimpleNamespace(diagnostics_json=None)
    context = SimpleNamespace(run=run)
    raw = "x" * 20_000
    metadata = {
        "result_mode": "repaired",
        "repair_method": "model_json",
        "warning": "结构已修复",
        "original_response_excerpt": raw[:12_000],
        "_diagnostic_raw": raw,
    }

    _capture_model_diagnostic(context, "characters", metadata)

    assert "_diagnostic_raw" not in metadata
    assert len(metadata["original_response_excerpt"]) == 12_000
    assert run.diagnostics_json[0]["raw_response"] == raw


def test_completed_generation_waits_for_author_confirmation() -> None:
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting")
    db.add(session)
    db.flush()
    operation = OperationRun(
        source_kind="novel_creation",
        source_id="generation-run",
        title="characters",
        status="running",
    )
    db.add(operation)
    db.flush()
    run = NovelCreationStageRun(
        session_id=session.id,
        stage="characters",
        operation="generate",
        status="running",
        storage_target="session_draft",
        operation_id=operation.id,
    )
    db.add(run)
    db.flush()

    complete_run(db, run, {"result_mode": "model"})
    db.flush()
    db.refresh(run)

    assert run.status == "waiting_user"
    assert run.events[-1].event_type == "waiting_user"
    operation = db.get(OperationRun, run.operation_id)
    assert operation is not None
    assert operation.status == "waiting_user"
    assert operation.can_pause is False
    assert operation.can_cancel is False
    assert operation.result_json["summary"] == run.current_message

    assert confirm_run(db, run) is True
    db.flush()
    db.refresh(run)
    assert run.status == "completed"
    assert run.events[-1].event_type == "author_confirmed"


def test_repeated_confirmation_is_idempotent_for_the_same_content() -> None:
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting")
    db.add(session)
    initialize_session_draft(session, {"preset_id": "free"})
    data = {
        "characters": [{"name": "林七", "role_type": "protagonist", "goal": "寻找真相"}],
        "relationships": [],
    }
    save_stage(session, "characters", data, confirm=False, source="model")
    db.commit()
    clicked_revision = int(session.revision or 0)
    payload = NovelCreationStageConfirmRequest(
        confirm=True,
        source="author",
        expected_revision=clicked_revision,
    )

    first = asyncio.run(confirm_creation_stage(session.id, "characters", payload, db))
    revision_after_first = int(session.revision or 0)
    assert session.draft_json["stages"]["characters"]["data"] == data
    second = asyncio.run(confirm_creation_stage(session.id, "characters", payload, db))

    assert first.code == 0
    assert second.code == 0
    assert second.message == "当前内容已经确认"
    assert int(session.revision or 0) == revision_after_first


def test_repeated_confirm_and_generate_replays_one_recommended_run() -> None:
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting")
    db.add(session)
    initialize_session_draft(session, {"preset_id": "free"})
    save_stage(session, "constraints", {"brief": "仙侠悬疑"}, confirm=True, source="author")
    save_stage(
        session,
        "concepts",
        {"options": [], "selected_concept_id": None},
        confirm=True,
        source="author",
    )
    save_stage(
        session,
        "world_style",
        {"writing_style": "克制", "worldbuilding": []},
        confirm=False,
        source="author",
    )
    db.commit()
    clicked_revision = int(session.revision or 0)
    payload = NovelCreationConfirmAndGenerateRequest(
        expected_revision=clicked_revision,
        use_model=False,
    )

    with patch("app.routers.novel_creation.schedule_creation_stage") as schedule:
        first = asyncio.run(
            confirm_and_generate_recommended(
                session.id,
                "world_style",
                payload,
                db,
                idempotency_key="confirm-and-next-once",
            )
        )
        revision_after_first = int(session.revision or 0)
        second = asyncio.run(
            confirm_and_generate_recommended(
                session.id,
                "world_style",
                payload,
                db,
                idempotency_key="confirm-and-next-once",
            )
        )

    assert first.data["action_type"] == "confirm_and_generate_recommended"
    assert first.data["recommended_stage"] == "characters"
    assert second.data["run"]["run_id"] == first.data["run"]["run_id"]
    assert second.data["recommended_stage"] == "characters"
    assert int(session.revision or 0) == revision_after_first
    assert db.query(NovelCreationRunClaim).filter_by(
        session_id=session.id,
        idempotency_key="confirm-and-next-once",
    ).count() == 1
    schedule.assert_called_once()


def test_restart_releases_interrupted_creation_run_for_retry() -> None:
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting")
    operation = OperationRun(
        source_kind="novel_creation",
        source_id="stage-1",
        title="stage",
        status="interrupted",
    )
    db.add_all([session, operation])
    db.flush()
    run = NovelCreationStageRun(
        id="stage-1",
        session_id=session.id,
        stage="characters",
        operation="generate",
        status="running",
        storage_target="session_draft",
        operation_id=operation.id,
    )
    claim = NovelCreationRunClaim(
        session_id=session.id,
        artifact_key="characters",
        idempotency_key="restart-claim",
        claim_token="claim-token",
        status="running",
        input_revision=0,
        input_snapshot_hash="snapshot",
    )
    db.add_all([run, claim])
    db.flush()
    run.claim_id = claim.id
    db.commit()

    assert mark_interrupted_novel_creation_runs(db) == 1
    db.commit()
    db.refresh(run)

    assert run.status == "interrupted"
    assert run.failure_class == "interrupted"
    assert run.events[-1].event_type == "interrupted"
    assert serialize_run(run)["run_id"] == run.id
    assert SqlAlchemyNovelCreationSessionStore(db).running_stage(session.id, "characters") is None
    assert db.get(NovelCreationRunClaim, claim.id).status == "interrupted"


def test_assistant_model_is_resolved_to_actual_provider_identity() -> None:
    with patch(
        "app.services.workspace.run_log.LLMGateway.model_identity",
        return_value=("openai", "gpt-actual"),
    ):
        assert resolve_assistant_model(None) == "openai:gpt-actual"


def test_v2_session_is_read_as_v3_exploration_without_mutating_stored_draft() -> None:
    session = NovelCreationSession(
        id="legacy-session",
        mode="internal_llm",
        status="drafting",
        schema_version=2,
        draft_json={
            "schema_version": 2,
            "form": {"brief": "legacy author idea"},
            "concepts": [{"id": "legacy-concept", "title": "kept"}],
            "stages": {},
        },
    )

    payload = serialize_session(session, include_runs=False)

    assert payload["schema_version"] == 3
    assert payload["draft"]["schema_version"] == 3
    assert payload["draft"]["creation_mode"] == "explore"
    assert payload["draft"]["concepts"][0]["title"] == "kept"
    assert session.schema_version == 2
    assert "creation_mode" not in session.draft_json
