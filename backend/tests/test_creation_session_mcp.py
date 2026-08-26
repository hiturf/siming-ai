from __future__ import annotations

import asyncio
import json

from app.mcp.adapter import execute_tool
from app.services.workspace.registry import registry
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def _payload(result) -> dict:
    return json.loads(result.content[0]["text"])


def test_creation_session_pack_exposes_only_conversational_creation_tools():
    names = {tool.name for tool in registry.list_for_mcp(permission_pack="creation_session")}

    assert "get_creation_snapshot" in names
    assert "patch_creation_session" in names
    assert "finalize_creation_session" in names
    assert "generate_creation_artifact" not in names
    assert "refine_creation_artifact" not in names
    assert "regenerate_creation_artifact" not in names
    assert "list_projects" not in names
    assert "delete_project" not in names
    assert "list_imported_files" not in names
    assert "read_imported_file" not in names


def test_creation_session_pack_injects_bound_session_and_rejects_cross_session_access():
    db = _db()
    authorized = _ready_session(db)
    other = _ready_session(db)

    allowed = asyncio.run(execute_tool(
        db,
        "",
        "get_creation_session",
        {},
        permission_pack="creation_session",
        creation_session_id=authorized.id,
    ))
    denied = asyncio.run(execute_tool(
        db,
        "",
        "get_creation_session",
        {"session_id": other.id},
        permission_pack="creation_session",
        creation_session_id=authorized.id,
    ))

    assert allowed.is_error is False
    assert allowed.content[0]["text"].startswith('{"status": "ok"')
    assert denied.is_error is True
    assert _payload(denied)["status"] == "denied"
    assert "scope mismatch" in _payload(denied)["detail"]


def test_creation_session_pack_requires_a_session_binding():
    db = _db()
    session = _ready_session(db)

    result = asyncio.run(execute_tool(
        db,
        "",
        "get_creation_session",
        {"session_id": session.id},
        permission_pack="creation_session",
    ))

    assert result.is_error is True
    assert "missing its required session binding" in _payload(result)["detail"]


def test_creation_session_pack_denies_model_spawning_tool_execution():
    db = _db()
    session = _ready_session(db)

    result = asyncio.run(execute_tool(
        db,
        "",
        "generate_creation_artifact",
        {"session_id": session.id, "artifact": "concepts"},
        permission_pack="creation_session",
        creation_session_id=session.id,
    ))

    assert result.is_error is True
    assert _payload(result)["status"] == "denied"
