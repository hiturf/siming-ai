"""Durable evidence regressions for workspace Direct-MCP calls."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.architecture.tool_categories import TOOL_CATEGORY_METADATA
from app.database.models import (
    AssistantRunStep,
    Base,
    ChapterDraft,
    Character,
    OutlineNode,
    Project,
)
from app.mcp.adapter import _log_mcp_tool_call, _safe_rollback
from app.mcp.server import _close_failed_scoped_workspace_step, handle_message, serve_stdio
from app.modules.assistant.infrastructure.models import AssistantConversation, AssistantRun
from app.modules.operations.infrastructure.models import OperationRun
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace
from app.services.tool_category_state import (
    activate_tool_categories,
    bind_tool_category_turn_guard,
    create_tool_category_state,
    read_tool_category_audits,
    remove_tool_category_state,
    replace_tool_categories,
)
from app.services.workspace.assistant_direct_mcp_turn import (
    DirectMcpCapture,
    WorkspaceDirectMcpTurn,
)
from app.services.workspace.assistant_turn_state import WorkspaceAssistantTurnState
from app.services.workspace.conversation_context_adapter import (
    workspace_execution_ledger_from_run_steps,
)
from app.services.workspace.direct_mcp_run_log import (
    DIRECT_MCP_CALL_KEY,
    begin_workspace_direct_mcp_step,
    issue_workspace_direct_mcp_lease,
)
from app.services.workspace.registry import registry
from app.services.workspace.run_log import finish_run_step
from app.services.workspace.terminal_draft_detection import local_cli_terminal_draft


@pytest.fixture(autouse=True)
def _keep_operation_events_in_test_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.workspace.run_log.record_operation_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.workspace.assistant_response.record_operation_signal",
        lambda *_args, **_kwargs: None,
    )


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _workspace_run(db, title: str) -> tuple[Project, AssistantConversation, AssistantRun]:
    project = Project(title=title)
    db.add(project)
    db.flush()
    conversation = AssistantConversation(
        project_id=project.id,
        title=f"{title} conversation",
        scope="project",
    )
    db.add(conversation)
    db.flush()
    run = AssistantRun(
        project_id=project.id,
        conversation_id=conversation.id,
        status="running",
        scope="project",
    )
    db.add(run)
    db.flush()
    operation = OperationRun(
        source_kind="assistant",
        source_id=run.id,
        project_id=project.id,
        title=title,
        status="running",
    )
    db.add(operation)
    db.flush()
    run.operation_id = operation.id
    db.commit()
    return project, conversation, run


def _lease(db, run: AssistantRun, *, iteration: int = 2) -> str:
    return issue_workspace_direct_mcp_lease(db, run, iteration=iteration)


def _scoped_state_file(
    project: Project,
    conversation: AssistantConversation,
    run: AssistantRun,
    *,
    iteration: int = 2,
    categories: list[str] | None = None,
) -> str:
    state_file = create_tool_category_state()
    bind_tool_category_turn_guard(
        state_file,
        {
            "kind": "workspace",
            "project_id": project.id,
            "conversation_id": conversation.id,
            "run_id": run.id,
            "iteration": iteration,
        },
    )
    replace_tool_categories(state_file, categories or ["story_knowledge"])
    activate_tool_categories(state_file)
    return state_file


def _create_character_call(
    project_id: str,
    *,
    call_id: int = 7,
    background: str = "守护山门的执事",
) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "create_character",
                "arguments": {
                    "project_id": project_id,
                    "name": "沈砚",
                    "background": background,
                },
            },
        },
        ensure_ascii=False,
    )


def _create_outline_nodes_call(project_id: str, *, call_id: int = 9) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "create_outline_nodes",
                "arguments": {
                    "project_id": project_id,
                    "nodes": [
                        {"title": "第一章 雾门", "node_type": "chapter"},
                        {"title": "第二章 石阶", "node_type": "chapter"},
                    ],
                },
            },
        },
        ensure_ascii=False,
    )


def _tool_call(tool_name: str, arguments: dict, *, call_id: int) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        ensure_ascii=False,
    )


def test_scoped_direct_mcp_write_persists_run_step_refs_and_replays_receipt() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP durable write")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        raw_call = _create_character_call(project.id)
        first = json.loads(
            handle_message(
                raw_call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert first["result"]["isError"] is False
        wire_payload = json.loads(first["result"]["content"][0]["text"])
        assert "current_version" not in (wire_payload.get("data") or {})

        steps = db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).all()
        characters = db.query(Character).filter(Character.project_id == project.id).all()
        assert len(steps) == 1
        assert len(characters) == 1
        step = steps[0]
        assert step.tool == "create_character"
        assert step.step_type == "write"
        assert step.iteration == 2
        assert step.status == "ok"
        assert json.loads(step.output_refs or "{}") == {
            "character": {
                "id": characters[0].id,
                "revision": 1,
            }
        }
        request = json.loads(step.request_json or "{}")
        assert request["project_id"] == project.id
        assert request["_context_execution_route"] == "external_mcp"
        assert request[DIRECT_MCP_CALL_KEY].startswith(f"direct_mcp:{run.id}:2:")

        ledger = workspace_execution_ledger_from_run_steps(
            conversation,
            (run,),
            tuple(steps),
            project_id=project.id,
        )
        assert len(ledger) == 1
        assert ledger[0].step_id == step.id
        assert [
            (reference.type, reference.id, reference.revision)
            for reference in ledger[0].resource_refs
        ] == [("character", characters[0].id, 1)]

        second = json.loads(
            handle_message(
                raw_call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert second["result"]["isError"] is False
        assert db.query(Character).filter(Character.project_id == project.id).count() == 1
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 1
        audits = read_tool_category_audits(state_file)
        assert audits[-1]["assistant_run_step_id"] == step.id
        assert audits[-1]["result_ref"] == f"assistant_run_step:{step.id}"
        assert audits[-1]["replayed"] is True

        changed = json.loads(
            handle_message(
                _create_character_call(
                    project.id,
                    background="同一个调用 ID 被换成了另一组参数",
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert changed["result"]["isError"] is True
        assert db.query(Character).filter(Character.project_id == project.id).count() == 1
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 1
        changed_audit = read_tool_category_audits(state_file)[-1]
        assert changed_audit["status"] == "denied"
        assert "不同参数" in changed_audit["result"]["detail"]
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_mcp_batch_write_refs_use_full_raw_result() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP outline refs")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        response = json.loads(
            handle_message(
                _create_outline_nodes_call(project.id),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert response["result"]["isError"] is False
        nodes = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project.id)
            .order_by(OutlineNode.sort_order.asc())
            .all()
        )
        step = (
            db.query(AssistantRunStep)
            .filter(
                AssistantRunStep.run_id == run.id,
                AssistantRunStep.tool == "create_outline_nodes",
            )
            .one()
        )
        assert json.loads(step.output_refs or "{}") == {
            "outline": [{"id": nodes[0].id}, {"id": nodes[1].id}]
        }
        raw_result = json.loads(step.result_json or "{}")
        assert [item["id"] for item in raw_result["data"]["nodes"]] == [
            nodes[0].id,
            nodes[1].id,
        ]
        ledger = workspace_execution_ledger_from_run_steps(
            conversation,
            (run,),
            (step,),
            project_id=project.id,
        )
        assert [reference.id for reference in ledger[0].resource_refs] == [
            nodes[0].id,
            nodes[1].id,
        ]
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_ready_direct_mcp_receipt_replays_as_usable_without_handler() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP ready replay")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=["story_knowledge"],
    )
    lease_token = _lease(db, run)
    call_id = 11
    arguments = {"project_id": project.id, "query": "主角"}
    started = begin_workspace_direct_mcp_step(
        db,
        state_file=state_file,
        project_id=project.id,
        tool_name="search_characters",
        arguments=dict(arguments),
        call_id=call_id,
        is_write=False,
        lease_token=lease_token,
    )
    ready_result = {
        "tool": "search_characters",
        "status": "ready",
        "detail": "上下文已准备",
        "data": {"items": []},
    }
    finish_run_step(
        db,
        started.step,
        status="ready",
        result=ready_result,
        detail=ready_result["detail"],
    )
    executor = AsyncMock()
    try:
        with patch("app.mcp.server.execute_tool", new=executor):
            response = json.loads(
                handle_message(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": call_id,
                            "method": "tools/call",
                            "params": {
                                "name": "search_characters",
                                "arguments": arguments,
                            },
                        }
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert response["result"]["isError"] is False
        replayed = json.loads(response["result"]["content"][0]["text"])
        assert replayed["status"] == "ready"
        executor.assert_not_awaited()
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 1
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_mcp_final_text_never_creates_write_evidence() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP prose is not evidence")
    state_file = _scoped_state_file(project, conversation, run, iteration=1)
    state = WorkspaceAssistantTurnState(
        db=db,
        project_id=project.id,
        payload=SimpleNamespace(
            model="opencode_cli:test",
            temperature=0.3,
            max_tokens=1_024,
        ),
        selected_provider="opencode_cli",
        supports_function_calling=False,
        local_cli_selected=True,
        local_cli_mcp_enabled=True,
        encode_event=lambda event: json.dumps(event, ensure_ascii=False),
        execute_action=AsyncMock(),
        prepare_context=AsyncMock(),
    )
    state.workspace = SqlAlchemyAssistantWorkspace(db)
    state.conversation = conversation
    state.assistant_run = run
    state.tool_category_state_file = state_file
    state.category_selected = True
    state.active_categories = ("story_knowledge",)
    state.observed_category_version = 1

    class _Gateway:
        @staticmethod
        def stream_chat_completion(**_kwargs):
            async def generate():
                yield "我已经写入角色 fake-character-id，revision=999。"

            return generate()

    async def collect() -> None:
        async for _event in WorkspaceDirectMcpTurn(state, _Gateway()).run(
            messages=[],
            iteration=1,
        ):
            pass

    try:
        asyncio.run(collect())
        steps = db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).all()
        assert steps == []
        assert db.query(Character).filter(Character.project_id == project.id).count() == 0
        assert workspace_execution_ledger_from_run_steps(
            conversation,
            (run,),
            (),
            project_id=project.id,
        ) == ()
        assert "fake-character-id" in state.final_reply
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_tampered_guard_cannot_redirect_fixed_mcp_project_or_run() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Bound MCP project")
    foreign_project, foreign_conversation, foreign_run = _workspace_run(db, "Foreign MCP project")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    bind_tool_category_turn_guard(
        state_file,
        {
            "kind": "workspace",
            "project_id": foreign_project.id,
            "conversation_id": foreign_conversation.id,
            "run_id": foreign_run.id,
            "iteration": 2,
        },
    )
    try:
        response = json.loads(
            handle_message(
                _create_character_call(project.id),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert response["result"]["isError"] is False
        step = db.query(AssistantRunStep).one()
        assert step.run_id == run.id
        assert step.project_id == project.id
        assert step.iteration == 2
        assert db.query(Character).filter(Character.project_id == project.id).count() == 1
        assert db.query(Character).filter(Character.project_id == foreign_project.id).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_mcp_preflight_denials_never_leave_running_steps() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP preflight")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=["story_knowledge"],
    )
    lease_token = _lease(db, run)
    try:
        unauthorized = json.loads(
            handle_message(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 31,
                        "method": "tools/call",
                        "params": {
                            "name": "delete_character",
                            "arguments": {
                                "project_id": project.id,
                                "character_id": "not-allowed",
                            },
                        },
                    }
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert unauthorized["result"]["isError"] is True
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 0

        replace_tool_categories(state_file, ["cataloging"])
        activate_tool_categories(state_file)
        handler = AsyncMock()
        with patch("app.services.workspace.executor.execute_workspace_action", new=handler):
            confirmation_denied = json.loads(
                handle_message(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 32,
                            "method": "tools/call",
                            "params": {
                                "name": "cancel_cataloging_job",
                                "arguments": {
                                    "project_id": project.id,
                                    "job_id": "job-1",
                                },
                            },
                        }
                    ),
                    db=db,
                    project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=lease_token,
                )
            )
        assert confirmation_denied["result"]["isError"] is True
        handler.assert_not_awaited()
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_lease_token_is_required_rotated_and_fenced_by_operation_state() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP lease fence")
    state_file = _scoped_state_file(project, conversation, run)
    old_token = _lease(db, run)
    handler = AsyncMock()
    try:
        with patch("app.mcp.server.execute_tool", new=handler):
            for call_id, token in ((41, ""), (42, "x" * 40)):
                response = json.loads(
                    handle_message(
                        _create_character_call(project.id, call_id=call_id),
                        db=db,
                        project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=token,
                    )
                )
                assert response["result"]["isError"] is True
        handler.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0

        new_token = _lease(db, run, iteration=3)
        with patch("app.mcp.server.execute_tool", new=handler):
            stale = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=43),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=old_token,
                )
            )
        assert stale["result"]["isError"] is True
        handler.assert_not_awaited()

        operation = db.get(OperationRun, run.operation_id)
        assert operation is not None
        operation.status = "cancelled"
        db.commit()
        with patch("app.mcp.server.execute_tool", new=handler):
            cancelled = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=44),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=new_token,
                )
            )
        assert cancelled["result"]["isError"] is True
        handler.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_lease_rejects_advanced_iteration_and_cross_project_conversation() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP owner fence")
    state_file = _scoped_state_file(project, conversation, run)
    token = _lease(db, run)
    try:
        run.current_iteration = 3
        db.commit()
        advanced = json.loads(
            handle_message(
                _create_character_call(project.id, call_id=51),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=token,
            )
        )
        assert advanced["result"]["isError"] is True
        assert db.query(AssistantRunStep).count() == 0

        token = _lease(db, run, iteration=4)
        foreign_project = Project(title="Foreign conversation project")
        db.add(foreign_project)
        db.flush()
        conversation.project_id = foreign_project.id
        db.commit()
        mismatched = json.loads(
            handle_message(
                _create_character_call(project.id, call_id=52),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=token,
            )
        )
        assert mismatched["result"]["isError"] is True
        assert db.query(AssistantRunStep).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_handler_exception_closes_once_and_replay_never_reexecutes(caplog) -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP exception")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    private_failure = "secret-token /private/project/chapter-content"
    executor = AsyncMock(side_effect=RuntimeError(private_failure))
    try:
        caplog.set_level(logging.ERROR, logger="app.mcp.adapter")
        with patch("app.services.workspace.executor.execute_workspace_action", new=executor):
            first = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=61),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
            replay = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=61),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert first["result"]["isError"] is True
        assert replay["result"]["isError"] is True
        executor.assert_awaited_once()
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.completed_at is not None
        assert step.output_refs is None
        assert db.query(Character).count() == 0
        assert private_failure not in caplog.text
        assert "secret-token" not in caplog.text
        assert "/private/project" not in caplog.text
        assert "RuntimeError" in caplog.text
        assert "traceback_code=" in caplog.text
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_commit_failure_logs_no_chained_secret_and_rolls_back() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP commit failure")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    private_failure = "secret manuscript /private/project/chapter.txt"
    try:
        with (
            patch(
                "app.mcp.adapter._safe_commit",
                side_effect=RuntimeError(private_failure),
            ),
            patch("app.mcp.server.logger.error") as safe_log,
        ):
            response = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=611),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )

        assert response["result"]["isError"] is True
        assert db.query(Character).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.output_refs is None
        rendered = repr(safe_log.call_args_list)
        assert private_failure not in rendered
        assert "/private/project" not in rendered
        assert "McpResultAuditError" in rendered
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_rollback_failure_log_contains_only_safe_exception_metadata(caplog) -> None:
    private_failure = "secret rollback content /private/project/chapter.txt"

    class FailingRollback:
        @staticmethod
        def rollback() -> None:
            raise RuntimeError(private_failure)

    caplog.set_level(logging.ERROR, logger="app.mcp.adapter")
    _safe_rollback(FailingRollback())

    assert private_failure not in caplog.text
    assert "/private/project" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "traceback_code=" in caplog.text


def test_direct_stream_error_log_does_not_emit_cli_stderr_or_secret(caplog) -> None:
    private_failure = "api_key=secret-value /private/mcp-config manuscript"

    async def failed_stream():
        if False:
            yield ""
        raise RuntimeError(private_failure)

    state = SimpleNamespace(
        payload=SimpleNamespace(model="opencode", temperature=0.3, max_tokens=100),
        local_cli_mcp_enabled=True,
        local_cli_extra_body={},
        assistant_run=SimpleNamespace(id="run-safe-log"),
        turn_telemetry=SimpleNamespace(report_model_activity=lambda *_a, **_k: None),
        event=lambda payload: payload,
    )
    gateway = SimpleNamespace(stream_chat_completion=lambda **_kwargs: failed_stream())
    turn = WorkspaceDirectMcpTurn(state, gateway)
    capture = DirectMcpCapture()

    async def collect() -> list[dict]:
        return [event async for event in turn._collect(capture, [])]

    caplog.set_level(
        logging.ERROR,
        logger="app.services.workspace.assistant_direct_mcp_turn",
    )
    events = asyncio.run(collect())

    assert capture.stream_error is not None
    assert events[-1]["tool"] == "stream_error"
    assert private_failure not in caplog.text
    assert "/private/mcp-config" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_failed_step_closure_preserves_concurrent_cancelled_winner() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP closure winner")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        started = begin_workspace_direct_mcp_step(
            db,
            state_file=state_file,
            project_id=project.id,
            tool_name="create_character",
            arguments={"project_id": project.id, "name": "沈砚"},
            call_id=612,
            is_write=True,
            lease_token=lease_token,
        )
        cancelled = {
            "tool": "create_character",
            "status": "cancelled",
            "detail": "newer author turn won",
            "data": {"reason": "turn_superseded"},
        }
        finish_run_step(
            db,
            started.step,
            status="cancelled",
            result=cancelled,
            detail=cancelled["detail"],
            error=cancelled["detail"],
            allow_partial_commit_refs=False,
        )

        restored = _close_failed_scoped_workspace_step(
            db,
            started,
            tool_name="create_character",
        )

        db.refresh(started.step)
        assert restored == cancelled
        assert started.step.status == "cancelled"
        assert started.step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_cancelled_executor_closes_intent_and_reraises_cancellation() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP cancellation")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    executor = AsyncMock(side_effect=asyncio.CancelledError())
    try:
        with (
            patch("app.services.workspace.executor.execute_workspace_action", new=executor),
            pytest.raises(asyncio.CancelledError),
        ):
            handle_message(
                _create_character_call(project.id, call_id=62),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        executor.assert_awaited_once()
        step = db.query(AssistantRunStep).one()
        assert step.status == "cancelled"
        assert step.completed_at is not None
        assert step.output_refs is None
        assert db.query(Character).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_post_handler_cas_rejection_rolls_back_resource_and_refs() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP CAS rejection")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        with patch("app.mcp.server.cas_workspace_direct_mcp_lease", return_value=False):
            response = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=71),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        payload = json.loads(response["result"]["content"][0]["text"])
        assert response["result"]["isError"] is True
        assert payload["status"] == "denied"
        assert db.query(Character).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "denied"
        assert step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_stale_finalize_claim_rolls_back_business_write_and_closes_error() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP finalize claim")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    from app.mcp import server as mcp_server

    finish_scoped = mcp_server._finish_scoped_workspace_step

    def stale_finish(session, started, result, payload):
        assert started is not None
        session.query(AssistantRunStep).filter(
            AssistantRunStep.id == started.step.id
        ).update({AssistantRunStep.status: "cancelled"}, synchronize_session=False)
        session.flush()
        return finish_scoped(session, started, result, payload)

    try:
        with patch(
            "app.mcp.server._finish_scoped_workspace_step",
            new=stale_finish,
        ):
            response = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=72),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert response["result"]["isError"] is True
        assert db.query(Character).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.completed_at is not None
        assert step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_partial_outline_batch_error_rolls_back_every_node_and_ref() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP partial batch")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    from app.services.workspace.tools import outline as outline_tools

    create_one = outline_tools.create_outline_node
    calls = 0

    async def fail_second(session, project_id, arguments):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await create_one(session, project_id, arguments)
        return {
            "tool": "create_outline_node",
            "status": "error",
            "detail": "injected second-node failure",
            "data": None,
        }

    try:
        with patch(
            "app.services.workspace.tools.outline.create_outline_node",
            new=fail_second,
        ):
            response = json.loads(
                handle_message(
                    _create_outline_nodes_call(project.id, call_id=73),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert response["result"]["isError"] is True
        assert calls == 2
        assert db.query(OutlineNode).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_concurrent_same_call_key_has_one_winner_and_one_handler(tmp_path) -> None:
    database_path = tmp_path / "direct-mcp-race.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    project, conversation, run = _workspace_run(setup, "Direct MCP concurrent claim")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(setup, run)
    project_id = str(project.id)
    setup.close()

    from app.services.workspace import direct_mcp_run_log as direct_log
    from app.services.workspace.executor import execute_workspace_action as execute_original

    claim_barrier = threading.Barrier(2)
    start_original = direct_log.start_run_step
    execution_lock = threading.Lock()
    execution_count = 0

    def synchronized_start(*args, **kwargs):
        claim_barrier.wait(timeout=10)
        return start_original(*args, **kwargs)

    async def counted_execute(*args, **kwargs):
        nonlocal execution_count
        with execution_lock:
            execution_count += 1
        return await execute_original(*args, **kwargs)

    def invoke() -> dict:
        session = Session()
        try:
            return json.loads(
                handle_message(
                    _create_character_call(project_id, call_id=81),
                    db=session,
                    project_id=project_id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        finally:
            session.close()

    try:
        with (
            patch(
                "app.services.workspace.direct_mcp_run_log.start_run_step",
                new=synchronized_start,
            ),
            patch(
                "app.services.workspace.executor.execute_workspace_action",
                new=counted_execute,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            responses = list(pool.map(lambda _index: invoke(), range(2)))

        verify = Session()
        try:
            assert execution_count == 1
            assert verify.query(AssistantRunStep).count() == 1
            assert verify.query(Character).count() == 1
            step = verify.query(AssistantRunStep).one()
            assert step.status == "ok"
            assert step.direct_mcp_call_key
            assert any(not item["result"]["isError"] for item in responses)
        finally:
            verify.close()
    finally:
        remove_tool_category_state(state_file)
        engine.dispose()


def test_draft_finalize_failure_has_no_phantom_cache_and_db_read_survives() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP draft UoW")
    outline = OutlineNode(
        project_id=project.id,
        title="第一章 雨门",
        node_type="chapter",
        status="pending",
        sort_order=1,
    )
    db.add(outline)
    db.commit()
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=["writing_context"],
    )
    lease_token = _lease(db, run)
    from app.mcp import server as mcp_server
    from app.services.workspace import generated_drafts

    finish_scoped = mcp_server._finish_scoped_workspace_step

    def stale_finish(session, started, result, payload):
        assert started is not None
        session.query(AssistantRunStep).filter(
            AssistantRunStep.id == started.step.id
        ).update({AssistantRunStep.status: "cancelled"}, synchronize_session=False)
        session.flush()
        return finish_scoped(session, started, result, payload)

    arguments = {
        "project_id": project.id,
        "content": "雨落石阶，沈砚推开山门。",
        "outline_node_id": outline.id,
        "context_manifest_id": "manifest-reviewed",
        "context_selection_token": "selection-reviewed",
    }
    isolated_cache: OrderedDict[str, dict] = OrderedDict()
    try:
        with (
            patch.object(generated_drafts, "_CHAPTER_DRAFTS", isolated_cache),
            patch(
                "app.services.workspace.tools.external_writing._external_draft_manifest_error",
                return_value=None,
            ),
        ):
            with patch(
                "app.mcp.server._finish_scoped_workspace_step",
                new=stale_finish,
            ):
                failed = json.loads(
                    handle_message(
                        _tool_call(
                            "save_external_chapter_draft",
                            arguments,
                            call_id=91,
                        ),
                        db=db,
                        project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=lease_token,
                    )
                )
            assert failed["result"]["isError"] is True
            assert db.query(ChapterDraft).count() == 0
            assert isolated_cache == {}

            saved = json.loads(
                handle_message(
                    _tool_call(
                        "save_external_chapter_draft",
                        arguments,
                        call_id=92,
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
            assert saved["result"]["isError"] is False
            draft = db.query(ChapterDraft).one()
            assert isolated_cache == {}
            saved_step = db.query(AssistantRunStep).filter_by(
                tool="save_external_chapter_draft",
                status="ok",
            ).one()
            assert json.loads(saved_step.output_refs or "{}") == {
                "chapter_draft": {"id": draft.id}
            }

            restored = json.loads(
                handle_message(
                    _tool_call(
                        "get_external_chapter_draft",
                        {"project_id": project.id, "draft_id": draft.id},
                        call_id=93,
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
            restored_payload = json.loads(restored["result"]["content"][0]["text"])
            assert restored["result"]["isError"] is False
            assert restored_payload["data"]["content"] == arguments["content"]
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_terminal_draft_requires_exact_run_iteration_output_ref() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP terminal refs")
    foreign_run = AssistantRun(
        project_id=project.id,
        conversation_id=conversation.id,
        status="running",
        scope="project",
    )
    draft = ChapterDraft(
        project_id=project.id,
        title="并发草稿",
        status="pending",
        content="只可由精确步骤归因",
    )
    db.add_all([foreign_run, draft])
    db.flush()
    refs = json.dumps({"chapter_draft": {"id": draft.id}}, ensure_ascii=False)
    foreign_step = AssistantRunStep(
        run_id=foreign_run.id,
        project_id=project.id,
        step_type="write",
        tool="save_external_chapter_draft",
        status="ok",
        iteration=3,
        output_refs=refs,
    )
    wrong_iteration = AssistantRunStep(
        run_id=run.id,
        project_id=project.id,
        step_type="write",
        tool="save_external_chapter_draft",
        status="ok",
        iteration=2,
        output_refs=refs,
    )
    db.add_all([foreign_step, wrong_iteration])
    db.commit()
    try:
        assert local_cli_terminal_draft(db, project.id, run.id, 3) is None
        wrong_iteration.iteration = 3
        db.commit()
        detected = local_cli_terminal_draft(db, project.id, run.id, 3)
        assert detected is not None
        assert detected[0]["tool"] == "save_external_chapter_draft"
        assert detected[0]["data"]["draft_id"] == draft.id
    finally:
        db.close()


def test_direct_pack_is_explicit_and_blocks_prompts_and_unsafe_tools() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP safe pack")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=list(TOOL_CATEGORY_METADATA),
    )
    lease_token = _lease(db, run)
    expected = {
        "search_characters",
        "search_chapters",
        "search_outline",
        "search_worldbuilding",
        "create_worldbuilding_entry",
        "update_worldbuilding_entry",
        "create_outline_node",
        "create_outline_nodes",
        "update_outline_node",
        "create_character",
        "update_character",
        "recall",
        "save_external_chapter_draft",
        "save_external_outline_draft",
        "get_external_chapter_draft",
    }
    unsafe = {
        "list_projects",
        "create_project",
        "import_file_as_project",
        "get_creation_session",
        "import_creation_material",
        "write_project_file",
        "export_project",
        "run_scheduled_task_now",
        "prepare_external_writing_context",
        "prepare_task_context",
        "search_task_context",
        "search_outline_tree",
    }
    executor = AsyncMock()
    try:
        direct_defs = registry.list_for_workspace_direct_mcp()
        assert {definition.name for definition in direct_defs} == expected
        assert all(
            definition.direct_mcp_project_scoped
            and definition.direct_mcp_transactional
            for definition in direct_defs
        )

        listed = json.loads(
            handle_message(
                _tool_call("unused", {}, call_id=94).replace(
                    '"method": "tools/call"', '"method": "tools/list"'
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        listed_names = {item["name"] for item in listed["result"]["tools"]}
        assert listed_names == expected | {"set_tool_categories"}

        with patch("app.services.workspace.executor.execute_workspace_action", new=executor):
            for call_id, tool_name in enumerate(sorted(unsafe), start=100):
                denied = json.loads(
                    handle_message(
                        _tool_call(
                            tool_name,
                            {"project_id": project.id, "path": "/tmp/escape"},
                            call_id=call_id,
                        ),
                        db=db,
                        project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=lease_token,
                    )
                )
                assert denied["result"]["isError"] is True
        executor.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0

        initialize = json.loads(
            handle_message(
                json.dumps({"jsonrpc": "2.0", "id": 120, "method": "initialize"}),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert "prompts" not in initialize["result"]["capabilities"]
        prompts = json.loads(
            handle_message(
                json.dumps({"jsonrpc": "2.0", "id": 121, "method": "prompts/list"}),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert prompts["result"]["prompts"] == []
        render = AsyncMock()
        with patch("app.mcp.server.render_prompt", new=render):
            denied_prompt = json.loads(
                handle_message(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 122,
                            "method": "prompts/get",
                            "params": {
                                "name": "writing_context",
                                "arguments": {"project_id": "foreign-project"},
                            },
                        }
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert denied_prompt["error"]["code"] != 0
        render.assert_not_awaited()
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_managed_cataloging_env_cannot_override_explicit_direct_workspace_pack() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP env precedence")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=list(TOOL_CATEGORY_METADATA),
    )
    lease_token = _lease(db, run)
    expected = {
        definition.name for definition in registry.list_for_workspace_direct_mcp()
    }
    stdin = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 140, "method": "tools/list"}),
                _tool_call(
                    "save_external_cataloging_facts",
                    {"project_id": project.id},
                    call_id=141,
                ),
            ]
        )
        + "\n"
    )
    stdout = io.StringIO()
    executor = AsyncMock()
    try:
        with (
            patch("app.mcp.server.sys.stdin", stdin),
            patch("app.mcp.server.sys.stdout", stdout),
            patch("app.mcp.server.get_compatible_env", return_value="cataloging"),
            patch(
                "app.services.workspace.executor.execute_workspace_action",
                new=executor,
            ),
        ):
            serve_stdio(
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )

        listed, denied = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert {item["name"] for item in listed["result"]["tools"]} == expected | {
            "set_tool_categories"
        }
        assert denied["result"]["isError"] is True
        executor.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_stdio_auto_permission_resolution_log_redacts_exception(caplog) -> None:
    private_failure = "database secret /private/project/transcript.db"
    stdin = io.StringIO("")
    stdout = io.StringIO()
    caplog.set_level(logging.WARNING, logger="app.mcp.server")

    with (
        patch("app.mcp.server.sys.stdin", stdin),
        patch("app.mcp.server.sys.stdout", stdout),
        patch("app.mcp.server.get_compatible_env", return_value=""),
        patch(
            "app.services.external_agent.permissions.resolve_effective_pack",
            side_effect=RuntimeError(private_failure),
        ),
    ):
        serve_stdio(db=object(), project_id="project-1", permission_pack="auto")

    assert private_failure not in caplog.text
    assert "/private/project" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_mcp_argument_logs_are_structurally_redacted(caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.mcp.adapter")
    secret = "secret-access-token-value"
    content = "完整章节正文不可进入日志"
    path = "/private/user/project/manuscript.txt"

    _log_mcp_tool_call(
        None,
        "project-1",
        "save_external_chapter_draft",
        {
            "accessToken": secret,
            "api_key": "api-key-value",
            "content": content,
            "path": path,
            "title": "也不记录任意字符串值",
            "limit": 10,
        },
        status="ok",
        detail="done",
    )

    rendered = caplog.text
    assert secret not in rendered
    assert "api-key-value" not in rendered
    assert content not in rendered
    assert path not in rendered
    assert "也不记录任意字符串值" not in rendered
    assert "[redacted]" in rendered
    assert "limit: 10" in rendered


def test_post_commit_state_audit_failure_replays_from_durable_winner() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP audit replay")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    call = _create_character_call(project.id, call_id=130)
    try:
        with (
            patch(
                "app.mcp.server._record_scoped_tool_result",
                side_effect=OSError("state file unavailable after commit"),
            ),
            pytest.raises(OSError, match="state file unavailable"),
        ):
            handle_message(
                call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        assert db.query(Character).count() == 1
        step = db.query(AssistantRunStep).one()
        assert step.status == "ok"
        assert step.output_refs is not None

        replay = json.loads(
            handle_message(
                call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert replay["result"]["isError"] is False
        assert db.query(Character).count() == 1
        assert db.query(AssistantRunStep).count() == 1
    finally:
        remove_tool_category_state(state_file)
        db.close()
