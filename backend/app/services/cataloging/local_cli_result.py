"""Result handling for one managed local-CLI cataloging turn."""
from __future__ import annotations

import json
from typing import Literal

from app.ai.local_cli_adapter import CLIStalledError
from app.architecture.uow import commit_session
from app.database.models import AgentRun, AgentRunEvent, CatalogingChapterRun, CatalogingJob
from app.database.session import SessionLocal
from app.services.cataloging.job_control import refresh_job_progress
from app.services.external_agent.run_service import add_event, update_run_status
from app.services.operation_runtime import record_operation_signal

_MAX_NO_SAVE_ATTEMPTS = 3
_TERMINAL_RUNS = {"completed", "completed_with_warnings", "skipped_by_user"}
TurnAction = Literal["next", "continue", "return"]


def agent_tool_event_count(
    agent_run_id: str,
    *,
    session_factory=SessionLocal,
) -> int:
    db = session_factory()
    try:
        return (
            db.query(AgentRunEvent.id)
            .filter(
                AgentRunEvent.run_id == agent_run_id,
                AgentRunEvent.event_type == "tool_start",
            )
            .count()
        )
    finally:
        db.close()


def _turn_has_no_saved_progress(stage: str, status: str) -> bool:
    if stage == "facts":
        return status in {"pending", "in_progress", "extracting"}
    if stage == "candidates":
        return status == "facts_saved"
    if stage == "apply":
        return status == "awaiting_confirmation"
    return False


def handle_cli_turn_exception(
    *,
    job_id: str,
    chapter_run_id: str,
    agent_run_id: str,
    stage: str,
    exc: Exception,
    session_factory=SessionLocal,
) -> None:
    db = session_factory()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        run = (
            db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.id == chapter_run_id)
            .first()
        )
        if not job or not run:
            return
        run.status = "failed"
        run.error = str(exc)
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.current_chapter_id = run.chapter_id
        job.error = run.error
        refresh_job_progress(db, job)
        add_event(
            db,
            agent_run_id,
            "chapter_agent_failed",
            status="error",
            message=run.error,
            payload_json=json.dumps({
                "job_id": job.id,
                "chapter_id": run.chapter_id,
                "chapter_run_id": run.id,
                "stage": stage,
            }, ensure_ascii=False),
        )
        commit_session(db)
        update_run_status(db, agent_run_id, "failed", summary=run.error)
        if job.operation_id:
            record_operation_signal(
                job.operation_id,
                "stalled" if isinstance(exc, CLIStalledError) else "error",
                {
                    "chapter_id": run.chapter_id,
                    "chapter_order": run.chapter_order,
                    "error": run.error,
                },
                message=run.error,
                db=db,
            )
    finally:
        db.close()


async def handle_cli_turn_result(
    *,
    job_id: str,
    chapter_run_id: str,
    agent_run_id: str,
    chapter_title: str,
    stage: str,
    returncode: int,
    stdout: str,
    stderr: str,
    tool_events_before: int,
    no_save_attempts: dict[str, int],
    session_factory=SessionLocal,
) -> TurnAction:
    db = session_factory()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        run = (
            db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.id == chapter_run_id)
            .first()
        )
        if not job or not run:
            return "return"
        add_event(
            db,
            agent_run_id,
            "chapter_agent_finished",
            status="ok" if returncode == 0 else "error",
            message=f"本机 CLI 已结束：{chapter_title}",
            payload_json=json.dumps({
                "returncode": returncode,
                "chapter_status": run.status,
                "stdout_tail": stdout[-1500:],
                "stderr_tail": stderr[-1500:],
            }, ensure_ascii=False),
        )
        tool_activity = (
            agent_tool_event_count(
                agent_run_id,
                session_factory=session_factory,
            )
            > tool_events_before
        )
        no_saved = returncode == 0 and _turn_has_no_saved_progress(stage, run.status)
        if no_saved:
            attempt = no_save_attempts.get(run.id, 0) + 1
            no_save_attempts[run.id] = attempt
            if attempt < _MAX_NO_SAVE_ATTEMPTS:
                if stage == "facts":
                    run.status = "pending"
                job.status = "running"
                job.blocked_chapter_id = None
                job.error = None
                prefix = (
                    "MCP 已连接，但模型本轮未调用任何 Siming 工具；"
                    if not tool_activity
                    else "模型调用了 Siming MCP，但未完成本章保存；"
                )
                add_event(
                    db,
                    agent_run_id,
                    "chapter_agent_retry",
                    status="running",
                    message=prefix + f"正在自动重试 {attempt + 1}/{_MAX_NO_SAVE_ATTEMPTS}",
                    payload_json=json.dumps({
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "chapter_run_id": run.id,
                        "stage": stage,
                        "attempt": attempt + 1,
                        "max_attempts": _MAX_NO_SAVE_ATTEMPTS,
                        "stdout_tail": stdout[-1500:],
                        "stderr_tail": stderr[-1500:],
                    }, ensure_ascii=False),
                )
                commit_session(db)
                return "continue"
        if returncode != 0:
            run.status = "failed"
            run.error = stderr[-2000:] or stdout[-2000:] or f"CLI exit code {returncode}"
        elif _turn_has_no_saved_progress(stage, run.status):
            run.status = "failed"
            run.error = (
                "MCP 已连接，但模型连续重试后仍未调用建档写入工具"
                if not tool_activity
                else "模型调用了 Siming MCP，但连续重试后仍未完成本章保存"
            )
        if run.status == "failed":
            job.status = "paused_on_failure"
            job.blocked_chapter_id = run.chapter_id
            job.error = run.error
            refresh_job_progress(db, job)
            commit_session(db)
            update_run_status(db, agent_run_id, "failed", summary=run.error)
            if job.operation_id:
                record_operation_signal(
                    job.operation_id,
                    "error",
                    {"chapter_id": run.chapter_id, "error": run.error},
                    message=run.error,
                    db=db,
                )
            return "return"
        if run.status == "awaiting_confirmation" and job.execution_mode == "manual":
            job.status = "waiting_confirmation"
            job.blocked_chapter_id = run.chapter_id
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
            if agent_run:
                agent_run.status = "waiting_confirmation"
                agent_run.current_step = f"等待确认：第 {run.chapter_order + 1} 章"
            refresh_job_progress(db, job)
            commit_session(db)
            return "return"
        commit_session(db)
        if job.operation_id and run.status in _TERMINAL_RUNS:
            record_operation_signal(
                job.operation_id,
                "checkpoint",
                {
                    "chapter_id": run.chapter_id,
                    "chapter_order": run.chapter_order,
                    "chapter_status": run.status,
                },
                message=f"第 {run.chapter_order + 1} 章已保存检查点",
                db=db,
            )
        return "next"
    finally:
        db.close()
