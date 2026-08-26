"""Durable process-local execution controls for novel-creation stage runs."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.architecture.uow import commit_session
from app.database.models import NovelCreationRunClaim, NovelCreationStageRun, OperationRun
from app.database.session import SessionLocal
from app.services.novel_creation_claims import complete_creation_claim
from app.services.novel_creation_runs import add_run_event
from app.services.operation_runtime import (
    activate_operation,
    finish_operation,
    heartbeat_loop,
    register_operation_actions,
    unregister_operation_actions,
    update_operation,
)
from app.services.workspace.tools.novel_creation_v2 import run_creation_artifact_generation
from app.modules.model_runtime.domain.configuration import ModelProviderConfig

_CREATION_TASKS: dict[str, asyncio.Task[Any]] = {}


def _task_for(run_id: str) -> asyncio.Task[Any] | None:
    task = _CREATION_TASKS.get(run_id)
    return task if task and not task.done() else None


async def _pause_running_task(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(NovelCreationStageRun, run_id)
        if not run or run.status not in {"queued", "running"}:
            return
        run.status = "paused"
        run.current_message = "任务已暂停；已完成检查点和草稿均已保留"
        run.next_action = "继续任务，或取消本轮并保留已有草稿"
        add_run_event(db, run, "paused", "paused", run.current_message, {"checkpoint_preserved": True})
        commit_session(db)
    finally:
        db.close()
    task = _task_for(run_id)
    if task:
        task.cancel()


async def _execute_creation_stage(
    run_id: str,
    session_id: str,
    request: dict[str, Any],
    request_provider: ModelProviderConfig | None = None,
) -> None:
    db = SessionLocal()
    heartbeat_task: asyncio.Task[Any] | None = None
    run: NovelCreationStageRun | None = None
    try:
        run = db.get(NovelCreationStageRun, run_id)
        operation_id = run.operation_id if run else None
        if operation_id:
            heartbeat_task = asyncio.create_task(heartbeat_loop(operation_id))
        if request_provider is None:
            with activate_operation(operation_id):
                await run_creation_artifact_generation(
                    db,
                    "",
                    {**request, "session_id": session_id, "_run_id": run_id},
                )
        else:
            # Mobile credentials are retained only by this in-memory task. The
            # run request stored in SQLite contains the selected model but no
            # encrypted envelope or plaintext key.
            from app.modules.model_runtime.application.request_override import use_request_provider

            with use_request_provider(request_provider), activate_operation(operation_id):
                await run_creation_artifact_generation(
                    db,
                    "",
                    {**request, "session_id": session_id, "_run_id": run_id},
                )
        _schedule_card_presentation(run_id, request_provider=request_provider)
    except asyncio.CancelledError:
        run = db.get(NovelCreationStageRun, run_id)
        if run and run.status in {"queued", "running"}:
            run.status = "cancelled"
            run.current_message = "立项任务已取消，已保存内容不会丢失"
            run.next_action = "检查当前草稿后可重新生成本阶段"
            run.completed_at = datetime.utcnow()
            add_run_event(db, run, "cancelled", "cancelled", run.current_message)
            complete_creation_claim(db, run.claim_id, error=run.current_message, status="cancelled")
            commit_session(db)
            if run.operation_id:
                finish_operation(run.operation_id, message=run.current_message, status="cancelled")
            _schedule_card_presentation(run_id)
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if run and run.operation_id:
            unregister_operation_actions(run.operation_id)
        _CREATION_TASKS.pop(run_id, None)
        db.close()


def _schedule_card_presentation(
    run_id: str,
    *,
    request_provider: ModelProviderConfig | None = None,
) -> None:
    from app.services.novel_creation_run_presentation import schedule_run_card_presentation

    schedule_run_card_presentation(run_id, request_provider=request_provider)


def schedule_creation_stage(
    run_id: str,
    session_id: str,
    request: dict[str, Any],
    *,
    operation_id: str | None = None,
    request_provider: ModelProviderConfig | None = None,
) -> asyncio.Task[Any]:
    existing = _task_for(run_id)
    if existing:
        return existing
    task = asyncio.create_task(
        _execute_creation_stage(
            run_id,
            session_id,
            request,
            request_provider=request_provider,
        )
    )
    _CREATION_TASKS[run_id] = task

    if operation_id:
        register_operation_actions(
            operation_id,
            cancel=task.cancel,
            pause=lambda: _pause_running_task(run_id),
        )
    return task


async def invoke_durable_creation_action(operation_id: str, action: str) -> bool:
    """Handle actions that must remain available after volatile handlers disappear."""
    db = SessionLocal()
    try:
        operation = db.get(OperationRun, operation_id)
        if not operation or operation.source_kind != "novel_creation":
            return False
        run = db.get(NovelCreationStageRun, operation.source_id)
        if not run:
            return False

        if action == "pause":
            if run.status not in {"queued", "running"}:
                return False
            await _pause_running_task(run.id)
            return True

        if action == "cancel":
            if run.status == "paused":
                run.status = "cancelled"
                run.current_message = "已取消暂停中的任务；已有草稿保持不变"
                run.next_action = "可重新生成本阶段"
                run.completed_at = datetime.utcnow()
                add_run_event(db, run, "cancelled", "cancelled", run.current_message)
                complete_creation_claim(db, run.claim_id, error=run.current_message, status="cancelled")
                commit_session(db)
                _schedule_card_presentation(run.id)
                return True
            task = _task_for(run.id)
            if task:
                task.cancel()
                return True
            return False

        if action == "continue":
            if run.status != "paused":
                return False
            from app.services.novel_creation_runs import invalidate_run_card_presentation

            invalidate_run_card_presentation(run)
            run.status = "running"
            run.current_message = "正在从最近检查点继续"
            run.next_action = None
            run.completed_at = None
            request = dict(run.request_json or {})
            request["_resume"] = True
            add_run_event(db, run, "continued", "running", run.current_message, {"resume_from_checkpoint": True})
            commit_session(db)
            schedule_creation_stage(run.id, run.session_id, request, operation_id=run.operation_id)
            return True

        if action == "retry_current_unit":
            if run.status not in {"failed", "cancelled", "interrupted"}:
                return False
            from app.services.novel_creation_runs import invalidate_run_card_presentation

            invalidate_run_card_presentation(run)
            claim = db.get(NovelCreationRunClaim, run.claim_id) if run.claim_id else None
            if claim:
                claim.status = "running"
                claim.error = None
                claim.result_json = None
                claim.completed_at = None
            run.status = "running"
            run.failure_class = None
            run.current_message = "正在重试未完成阶段"
            run.next_action = None
            run.completed_at = None
            request = dict(run.request_json or {})
            request["_resume"] = True
            add_run_event(db, run, "retrying", "running", run.current_message, {"resume_from_checkpoint": True})
            update_operation(
                db,
                operation,
                status="running",
                health_status="active",
                message=run.current_message,
                event_type="retrying",
                attention={},
                result={},
            )
            try:
                commit_session(db)
            except IntegrityError:
                db.rollback()
                return False
            schedule_creation_stage(run.id, run.session_id, request, operation_id=run.operation_id)
            return True
        return False
    finally:
        db.close()


__all__ = ["invoke_durable_creation_action", "schedule_creation_stage"]
