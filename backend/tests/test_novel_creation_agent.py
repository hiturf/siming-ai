from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services.novel_creation_agent import run_creation_agent
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def test_creation_agent_lets_model_read_then_call_any_creation_tool():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-generate",
                "type": "function",
                "function": {
                    "name": "generate_creation_artifact",
                    "arguments": json.dumps({
                        "artifact": "world_style",
                        "entity_type": "worldbuilding",
                        "instruction": "新增用户描述的两条修炼规则",
                    }, ensure_ascii=False),
                },
            }],
        },
        {"content": "已读取当前设定，并开始新增修炼规则。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {"tool": "get_creation_snapshot", "status": "ok", "data": {"revision": session.revision}},
        {"tool": "generate_creation_artifact", "status": "ok", "data": {"run": {"id": "run-1", "status": "running"}}},
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="在世界观里加入两条修炼规则",
            model="openai:test",
            history=[{"role": "user", "content": "这是仙侠小说"}],
        ))

    read_action = executor.call_args_list[0].args[2]
    write_action = executor.call_args_list[1].args[2]
    assert read_action["tool"] == "get_creation_snapshot"
    assert read_action["arguments"]["session_id"] == session.id
    assert write_action["tool"] == "generate_creation_artifact"
    assert write_action["arguments"]["session_id"] == session.id
    assert write_action["arguments"]["expected_revision"] == session.revision
    assert write_action["arguments"]["model"] == "openai:test"
    assert result["run"]["id"] == "run-1"
    assert "开始新增" in result["reply"]


def test_creation_agent_rejects_non_creation_tools_even_if_model_requests_one():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
        {
            "content": "",
            "tool_calls": [{
                "id": "call-invalid",
                "type": "function",
                "function": {"name": "delete_project", "arguments": "{}"},
            }],
        },
        {"content": "没有执行越权操作。", "tool_calls": []},
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db, session=session, message="继续处理立项", model="openai:test",
        ))

    assert result["tool_results"][0]["status"] == "skipped"
    assert "不属于立项会话" in result["tool_results"][0]["detail"]


def test_local_cli_provider_uses_isolated_in_chat_tool_bridge():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(return_value={
        "content": json.dumps({"tool_calls": [], "reply": "我可以继续协助完善世界观。"}, ensure_ascii=False),
        "tool_calls": [],
    })
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        return_value={"local_cli_isolated": True, "local_cli_allow_mcp": False},
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成文风与世界观，基调要厚重史诗",
            model="opencode_cli:opencode/deepseek-v4-flash-free",
        ))

    first_request = completion.call_args_list[0].kwargs
    assert first_request["tools"] == []
    assert first_request["extra_body"]["local_cli_isolated"] is True
    assert first_request["extra_body"]["local_cli_allow_mcp"] is False
    assert first_request["extra_body"].get("local_cli_timeout_seconds", 600) == 600
    assert [item["role"] for item in first_request["messages"]].count("system") == 1
    assert "内化在司命聊天窗口" in first_request["messages"][0]["content"]
    assert "受控 JSON 桥" in first_request["messages"][0]["content"]
    assert "patch_creation_artifact" in first_request["messages"][0]["content"]
    assert "不会扫描或修改该 CLI 的全局配置" in first_request["messages"][0]["content"]
    executor.assert_not_awaited()
    assert result["write_count"] == 0
    assert "继续协助" in result["reply"]


def test_non_opencode_cli_bridge_executes_allowlisted_write_after_one_turn_grant():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
        {
            "content": json.dumps({
                "tool_calls": [{
                    "name": "patch_creation_session",
                    "arguments": {"changes": {"target_words": 2_500_000, "target_chapters": 1000}},
                }],
                "reply": "",
            }, ensure_ascii=False),
            "tool_calls": [],
        },
        {
            "content": json.dumps({
                "tool_calls": [{"name": "get_creation_session", "arguments": {}}],
                "reply": "",
            }, ensure_ascii=False),
            "tool_calls": [],
        },
        {
            "content": json.dumps({
                "tool_calls": [],
                "reply": "已把目标字数改为250万字、目标章节改为1000章，并完成复查。",
            }, ensure_ascii=False),
            "tool_calls": [],
        },
    ])
    executor = AsyncMock(side_effect=[
        {"tool": "patch_creation_session", "status": "ok", "detail": "立项目标已更新"},
        {"tool": "get_creation_session", "status": "ok", "data": {"revision": session.revision + 1}},
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="claude_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        return_value={"local_cli_isolated": True, "local_cli_allow_mcp": False},
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="把测试写入创作约束",
            model="claude_cli:claude-code",
            local_cli_write_granted=True,
        ))

    assert result["write_count"] == 1
    assert executor.call_args_list[0].args[2]["tool"] == "patch_creation_session"
    assert executor.call_args_list[0].args[2]["arguments"]["session_id"] == session.id
    assert executor.call_args_list[0].args[2]["arguments"]["expected_revision"] == session.revision
    assert result["permission_required"] is False
    assert "250万字" in result["reply"]


def test_opencode_one_turn_grant_uses_direct_session_scoped_mcp():
    db = _db()
    session = _ready_session(db)
    baseline_revision = int(session.revision or 0)
    captured_request: dict = {}

    async def completion(**kwargs):
        captured_request.update(kwargs)
        db.query(type(session)).filter(type(session).id == session.id).update(
            {"revision": baseline_revision + 1},
            synchronize_session=False,
        )
        db.commit()
        return {
            "content": "已通过临时 Siming MCP 更新立项目标，并回读确认 revision 已变化。",
            "tool_calls": [],
        }

    executor = AsyncMock()
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="把目标改为250万字和1000章",
            model="opencode_cli:opencode/deepseek-v4-flash-free",
            local_cli_write_granted=True,
            local_cli_read_paths=[r"D:\references\brief.md"],
        ))

    executor.assert_not_awaited()
    assert result["write_count"] == 1
    assert result["tool_results"][0]["tool"] == "mcp_verified_write"
    assert result["permission_required"] is False
    assert captured_request["tools"] == []
    assert captured_request["extra_body"]["local_cli_isolated"] is True
    assert captured_request["extra_body"]["local_cli_permission_granted"] is True
    assert captured_request["extra_body"]["local_cli_allow_mcp"] is True
    assert captured_request["extra_body"]["local_cli_read_permission_granted"] is True
    assert captured_request["extra_body"]["local_cli_read_paths"] == [r"D:\references\brief.md"]
    assert captured_request["extra_body"]["local_cli_mcp_permission_pack"] == "creation_session"
    assert captured_request["extra_body"]["local_cli_mcp_creation_session_id"] == session.id
    assert "临时 Siming MCP" in captured_request["messages"][0]["content"]
    assert "siming_turn_get_creation_snapshot" in captured_request["messages"][0]["content"]


def test_local_cli_bridge_blocks_write_without_one_turn_grant():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
        {
            "content": json.dumps({
                "tool_calls": [{
                    "name": "patch_creation_session",
                    "arguments": {"changes": {"target_words": 2_500_000}},
                }],
                "reply": "",
            }, ensure_ascii=False),
            "tool_calls": [],
        },
        {
            "content": json.dumps({
                "tool_calls": [],
                "reply": "本轮没有写入授权，所以没有修改立项数据。",
            }, ensure_ascii=False),
            "tool_calls": [],
        },
    ])
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        return_value={"local_cli_isolated": True, "local_cli_allow_mcp": False},
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="写入测试",
            model="opencode_cli:opencode/deepseek-v4-flash-free",
        ))

    executor.assert_not_awaited()
    assert result["write_count"] == 0
    assert result["permission_required"] is True
    assert result["tool_results"][0]["status"] == "permission_required"
    assert "没有写入授权" in result["reply"]


def test_creation_agent_resolves_default_model_once_and_propagates_it_to_generation():
    from types import SimpleNamespace

    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read-default-model",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-generate-default-model",
                "type": "function",
                "function": {
                    "name": "generate_creation_artifact",
                    "arguments": json.dumps({
                        "artifact": "concepts",
                        "model": None,
                        "use_model": False,
                    }),
                },
            }],
        },
        {"content": "已使用当前有效模型开始生成创意方向。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {"tool": "get_creation_snapshot", "status": "ok", "data": {"revision": session.revision}},
        {
            "tool": "generate_creation_artifact",
            "status": "running",
            "data": {"run": {"id": "run-model", "status": "running"}},
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.select_model_for_task",
        return_value=SimpleNamespace(model="openai:resolved-default"),
    ) as select_model, patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=True,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="openai",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成创意方向",
            model=None,
        ))

    select_model.assert_called_once_with(
        task_type="novel_creation",
        model_override=None,
    )
    assert completion.call_args_list[0].kwargs["model"] == "openai:resolved-default"
    write_arguments = executor.call_args_list[1].args[2]["arguments"]
    assert write_arguments["model"] == "openai:resolved-default"
    assert write_arguments["use_model"] is True
    assert result["run"]["id"] == "run-model"
