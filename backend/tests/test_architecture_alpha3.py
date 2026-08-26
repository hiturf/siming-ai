"""Boundary tests for the 3.0 alpha.3 runtime extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    ContextRebuildJob,
    ContextRebuildProject,
    Project,
)
from app.modules.context.infrastructure import rebuild as context_rebuild
from app.modules.context.infrastructure.rebuild import ContextRebuildRunner
from app.modules.model_runtime.application.runtime import ModelRuntime
from app.modules.model_runtime.domain.configuration import (
    ModelProviderConfig,
    TaskModelSetting,
)
from app.modules.operations.domain.failures import classify_failure
from app.modules.operations.domain.state import default_outcome, project_lifecycle_status


class FakeModelConfigurations:
    def __init__(self) -> None:
        self.global_config = ModelProviderConfig(
            provider="openai",
            default_model="writer-model",
            api_key="secret",
        )
        self.local_config = ModelProviderConfig(
            provider="local_llama_cpp",
            default_model="local-writer",
            api_key="local",
            provider_type="local_runtime",
        )
        self.task_model = TaskModelSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="local-writer",
            context_length=32768,
        )
    def global_default(self) -> ModelProviderConfig | None:
        return self.global_config

    def ready_providers(self) -> tuple[ModelProviderConfig, ...]:
        return (self.global_config, self.local_config)

    def provider(self, provider: str) -> ModelProviderConfig | None:
        return next(
            (item for item in self.ready_providers() if item.provider == provider),
            None,
        )

    def task_setting(self, task_type: str) -> TaskModelSetting | None:
        return self.task_model if task_type == self.task_model.task_type else None


def test_model_runtime_prefers_task_default_and_applies_local_context():
    configurations = FakeModelConfigurations()
    runtime = ModelRuntime(configurations)

    with patch(
        "app.modules.model_runtime.application.runtime.local_runtime_disabled",
        return_value=False,
    ):
        request_metadata = {"request": "chapter"}
        task_specific = runtime.select_for_task(
            task_type="writing",
            extra_body=request_metadata,
        )

    assert task_specific.model == "local_llama_cpp:local-writer"
    assert task_specific.source == "task_setting"
    assert request_metadata["moshu_context_length"] == 32768


def test_model_runtime_returns_provider_snapshots_without_mutating_readiness():
    configurations = FakeModelConfigurations()
    runtime = ModelRuntime(configurations)

    config = runtime.provider_config("openai")

    assert config.default_model == "writer-model"


def test_operation_state_and_failure_classification_are_shared_contracts():
    assert project_lifecycle_status("waiting_confirmation") == "waiting_user"
    assert default_outcome("completed", {"reply": "done"}) == "completed_with_reply"
    assert classify_failure("Free usage exceeded, retrying later") == "quota_or_rate_limit"

    from app.services.operation_runtime import (
        activate_operation,
        current_operation_id,
        iterate_with_operation,
    )

    assert callable(activate_operation)
    assert callable(current_operation_id)
    assert callable(iterate_with_operation)


def test_context_rebuild_commits_each_project_checkpoint_independently():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                Project(id="p1", title="First project", writing_style="natural"),
                Project(id="p2", title="Second project", writing_style="natural"),
                ContextRebuildJob(id="job", total_projects=2),
                ContextRebuildProject(id="row-1", job_id="job", project_id="p1"),
                ContextRebuildProject(id="row-2", job_id="job", project_id="p2"),
            ]
        )
        db.commit()

    class FakeOrchestrator:
        def __init__(self, _db) -> None:
            pass

        def _sync_rebuild_operation(self, _job, *, message: str) -> None:
            assert message

        def build_semantic_embeddings(self, project_id: str) -> dict:
            if project_id == "p2":
                raise RuntimeError("semantic index unavailable")
            return {"indexed": 2}

    def lexical_reindexer(_db, project_id: str) -> dict:
        return {"total_chunks": 3 if project_id == "p1" else 1}

    runner = ContextRebuildRunner(
        orchestrator_factory=FakeOrchestrator,
        lexical_reindexer=lexical_reindexer,
    )
    with (
        patch.object(context_rebuild, "SessionLocal", Session),
        patch.object(context_rebuild, "checkpoint_operation"),
    ):
        runner.run("job")

    with Session() as db:
        job = db.get(ContextRebuildJob, "job")
        first = db.get(ContextRebuildProject, "row-1")
        second = db.get(ContextRebuildProject, "row-2")
        assert job is not None and job.status == "failed"
        assert job.completed_projects == 1
        assert job.failed_projects == 1
        assert first is not None and first.status == "completed"
        assert first.indexed_chunks == 3
        assert first.semantic_chunks == 2
        assert second is not None and second.status == "failed"
        assert "semantic index unavailable" in (second.error or "")

    engine.dispose()


def test_alpha3_runtime_boundaries_do_not_reintroduce_legacy_dependencies():
    backend_root = Path(__file__).resolve().parents[1]
    gateway = (
        backend_root / "app/modules/model_runtime/infrastructure/gateway.py"
    ).read_text(encoding="utf-8")
    operations_router = (backend_root / "app/routers/operations.py").read_text(encoding="utf-8")
    context_service = (backend_root / "app/services/context_orchestrator.py").read_text(
        encoding="utf-8"
    )

    assert "SessionLocal" not in gateway
    assert "database.models" not in operations_router
    assert "ai.gateway" not in context_service
