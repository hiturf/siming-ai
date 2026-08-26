"""MCP server protocol handler.

Processes JSON-RPC messages for the MCP protocol. This module handles
the message framing and dispatches to adapter/permissions layers.

V1 serves over stdio only.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

from app.architecture.uow import commit_session
from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    tool_category_controller_schema,
)
from app.core.legacy_env import get_compatible_env
from app.mcp.adapter import execute_tool, list_mcp_tools
from app.mcp.prompts import list_prompts, render_prompt
from app.mcp.schemas import McpToolResult, make_text_result
from app.modules.creation.interfaces.agent_progress import (
    creation_tool_completed_event,
    creation_tool_started_event,
)
from app.modules.creation.interfaces.agent_scope import CREATION_AGENT_WRITE_TOOL_NAMES
from app.services.tool_category_state import (
    append_tool_category_audit,
    append_tool_category_event,
    creation_turn_write_denial_for_state,
    creation_turn_write_tools_closed,
    read_tool_category_state,
    record_creation_turn_write_result,
    replace_tool_categories,
)
from app.services.workspace.registry import registry
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

# ── MCP protocol constants ───────────────────────────────────────────────

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "siming"
SERVER_VERSION = APP_VERSION

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TOOL_NOT_FOUND = -32000
PERMISSION_DENIED = -32001
PROJECT_NOT_FOUND = -32002
TOOL_EXECUTION_FAILED = -32003


def _jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> str:
    """Build a JSON-RPC error response string."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    resp = {"jsonrpc": "2.0", "id": id, "error": err}
    # Keep the wire payload ASCII-safe for Windows stdio MCP clients. JSON
    # parsers still recover the original Unicode strings after decoding.
    return json.dumps(resp, ensure_ascii=True)


def _jsonrpc_result(id: Any, result: Any) -> str:
    """Build a JSON-RPC success response string."""
    resp = {"jsonrpc": "2.0", "id": id, "result": result}
    # Keep the wire payload ASCII-safe for Windows stdio MCP clients. JSON
    # parsers still recover the original Unicode strings after decoding.
    return json.dumps(resp, ensure_ascii=True)


def _configure_stdio_utf8() -> None:
    """Prefer UTF-8 stdio when the host process supports reconfiguration."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def handle_message(
    raw: str,
    *,
    db: Any = None,
    project_id: str = "",
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
) -> str:
    """Process one JSON-RPC message and return the response string.

    Args:
        raw: The raw JSON-RPC message string.
        db: SQLAlchemy session (required for tools/call).
        project_id: Current project ID (required for tools/call).
        allowed_tiers: Permission tiers to allow. Defaults to {"readonly"}.
        permission_pack: Permission pack name. If set, overrides allowed_tiers.
        creation_session_id: Session boundary required by the creation_session pack.

    Returns:
        JSON-RPC response string.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return _jsonrpc_error(None, PARSE_ERROR, "Invalid JSON")

    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if method == "initialize":
        return _handle_initialize(msg_id, params)
    elif method == "tools/list":
        return _handle_tools_list(
            msg_id,
            allowed_tiers,
            permission_pack,
            tool_category_state_file,
        )
    elif method == "tools/call":
        return _handle_tools_call(
            msg_id,
            params,
            db,
            project_id,
            allowed_tiers,
            permission_pack,
            creation_session_id,
            tool_category_state_file,
        )
    elif method == "prompts/list":
        if permission_pack == "creation_session":
            return _jsonrpc_result(msg_id, {"prompts": []})
        return _handle_prompts_list(msg_id)
    elif method == "prompts/get":
        if permission_pack == "creation_session":
            return _jsonrpc_error(
                msg_id,
                PERMISSION_DENIED,
                "Prompts are not exposed in a creation-only turn",
            )
        return _handle_prompts_get(msg_id, params, db)
    elif method == "ping":
        return _jsonrpc_result(msg_id, {})
    else:
        return _jsonrpc_error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def _handle_initialize(msg_id: Any, params: dict) -> str:
    """Handle the MCP initialize handshake."""
    result = {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }
    return _jsonrpc_result(msg_id, result)


def _handle_tools_list(
    msg_id: Any,
    allowed_tiers: set[str],
    permission_pack: str | None = None,
    tool_category_state_file: str = "",
) -> str:
    """Handle tools/list request."""
    tools = list_mcp_tools(allowed_tiers=allowed_tiers, permission_pack=permission_pack)
    if tool_category_state_file:
        try:
            state = read_tool_category_state(tool_category_state_file)
        except ValueError as exc:
            return _jsonrpc_error(msg_id, PERMISSION_DENIED, str(exc))
        enabled = set(state.get("active_categories") or [])
        tools = [
            tool for tool in tools
            if (definition := registry.get(tool.name)) is not None
            and definition.agent_category in enabled
        ]
        if permission_pack == "creation_session" and creation_turn_write_tools_closed(state):
            tools = [tool for tool in tools if tool.name not in CREATION_AGENT_WRITE_TOOL_NAMES]
    tool_dicts = []
    if tool_category_state_file:
        controller = tool_category_controller_schema()["function"]
        tool_dicts.append({
            "name": controller["name"],
            "description": controller["description"],
            "inputSchema": controller["parameters"],
        })
    for t in tools:
        tool_dicts.append({
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        })
    return _jsonrpc_result(msg_id, {"tools": tool_dicts})


def _handle_prompts_list(msg_id: Any) -> str:
    """Handle prompts/list request."""
    prompts = []
    for prompt in list_prompts():
        prompts.append({
            "name": prompt.name,
            "description": prompt.description,
            "arguments": [
                {
                    "name": arg.name,
                    "description": arg.description,
                    "required": arg.required,
                }
                for arg in prompt.args
            ],
        })
    return _jsonrpc_result(msg_id, {"prompts": prompts})


def _handle_prompts_get(msg_id: Any, params: dict, db: Any) -> str:
    """Handle prompts/get request."""
    if db is None:
        return _jsonrpc_error(msg_id, INTERNAL_ERROR, "Database session not available for prompt rendering")
    name = str(params.get("name") or "").strip()
    arguments = params.get("arguments", {})
    if not name:
        return _jsonrpc_error(msg_id, INVALID_PARAMS, "Prompt name is required")
    if not isinstance(arguments, dict):
        arguments = {}
    messages = render_prompt(db, name, {str(k): str(v) for k, v in arguments.items()})
    if messages is None:
        return _jsonrpc_error(msg_id, METHOD_NOT_FOUND, f"Prompt not found: {name}")
    # Governed prompt rendering can prepare a persisted baseline manifest.
    # Commit it before handing the ID to an MCP client so a later evidence or
    # formal-write call can validate the exact same sources.
    commit_session(db)
    return _jsonrpc_result(msg_id, {
        "description": f"Siming prompt: {name}",
        "messages": [
            {
                "role": message.role,
                "content": {"type": "text", "text": message.content},
            }
            for message in messages
        ],
    })


def _category_scoped_call_result(
    tool_name: str,
    arguments: dict[str, Any],
    tool_category_state_file: str,
) -> McpToolResult | None:
    """Handle the category controller and reject tools outside the active set."""

    try:
        state = read_tool_category_state(tool_category_state_file)
    except ValueError as exc:
        return make_text_result(json.dumps({"status": "denied", "detail": str(exc)}, ensure_ascii=False), is_error=True)
    if tool_name == TOOL_CATEGORY_CONTROLLER:
        try:
            payload = replace_tool_categories(
                tool_category_state_file,
                arguments.get("enabled_categories"),
            )
        except ValueError as exc:
            payload = {
                "tool": tool_name,
                "status": "error",
                "detail": str(exc),
                "data": None,
            }
        append_tool_category_audit(tool_category_state_file, {
            "tool": tool_name,
            "arguments": arguments,
            "status": payload.get("status"),
            "result": payload,
        })
        return make_text_result(
            json.dumps(payload, ensure_ascii=False),
            is_error=payload.get("status") != "ok",
        )
    definition = registry.get(tool_name)
    enabled = set(state.get("active_categories") or [])
    category_change_pending = int(state.get("active_version") or 0) < int(state.get("version") or 0)
    if category_change_pending or definition is None or definition.agent_category not in enabled:
        payload = {
            "tool": tool_name,
            "status": "denied",
            "detail": (
                "工具类别已经切换，当前模型步骤已结束"
                if category_change_pending
                else "该工具所属类别当前未开放"
            ),
        }
        append_tool_category_audit(tool_category_state_file, {
            "tool": tool_name,
            "arguments": arguments,
            "status": "denied",
            "result": payload,
        })
        return make_text_result(
            json.dumps(payload, ensure_ascii=False),
            is_error=True,
        )
    return None


def _tool_result_payload(result: McpToolResult, tool_name: str) -> dict[str, Any]:
    for block in result.content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            payload = json.loads(str(block.get("text") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {
        "tool": tool_name,
        "status": "error" if result.is_error else "ok",
        "detail": "工具执行失败" if result.is_error else "工具执行完成",
    }


def _creation_turn_write_scoped_call_result(
    tool_name: str,
    arguments: dict[str, Any],
    tool_category_state_file: str,
) -> McpToolResult | None:
    """Enforce the one-user-message mutation boundary before execution."""

    if tool_name not in CREATION_AGENT_WRITE_TOOL_NAMES:
        return None
    try:
        payload = creation_turn_write_denial_for_state(
            tool_category_state_file,
            tool_name,
        )
    except ValueError as exc:
        payload = {
            "tool": tool_name,
            "status": "denied",
            "detail": str(exc),
            "data": {"reason": "invalid_turn_state"},
        }
    if payload is None:
        return None
    append_tool_category_audit(tool_category_state_file, {
        "tool": tool_name,
        "arguments": arguments,
        "status": "denied",
        "result": payload,
    })
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    append_tool_category_event(tool_category_state_file, {
        "type": "tool_completed",
        "message": str(payload.get("detail") or "本轮写工具已经关闭"),
        "data": {
            "tool": tool_name,
            "status": "denied",
            "turn_boundary": str(data.get("reason") or "write_limit"),
        },
    })
    return make_text_result(json.dumps(payload, ensure_ascii=False), is_error=True)


def _record_scoped_tool_result(
    *,
    tool_category_state_file: str,
    permission_pack: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    result_payload: dict[str, Any],
) -> None:
    """Audit one scoped result and advance the shared creation write budget."""

    append_tool_category_audit(tool_category_state_file, {
        "tool": tool_name,
        "arguments": arguments,
        "status": result_payload.get("status"),
        "result": result_payload,
    })
    if permission_pack != "creation_session":
        return
    append_tool_category_event(
        tool_category_state_file,
        creation_tool_completed_event(tool_name, arguments, result_payload),
    )
    boundary_event = record_creation_turn_write_result(
        tool_category_state_file,
        tool_name,
        result_payload,
    )
    if boundary_event is not None:
        append_tool_category_event(tool_category_state_file, boundary_event)


async def _handle_tools_call_async(
    msg_id: Any,
    params: dict,
    db: Any,
    project_id: str,
    allowed_tiers: set[str],
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
) -> str:
    """Handle tools/call request — async version for actual execution."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if not isinstance(arguments, dict):
        arguments = {}

    if tool_category_state_file:
        scoped_result = _category_scoped_call_result(
            tool_name,
            arguments,
            tool_category_state_file,
        )
        if scoped_result is not None:
            return _jsonrpc_result(msg_id, _tool_result_to_dict(scoped_result))
        if permission_pack == "creation_session":
            write_scoped_result = _creation_turn_write_scoped_call_result(
                tool_name,
                arguments,
                tool_category_state_file,
            )
            if write_scoped_result is not None:
                return _jsonrpc_result(msg_id, _tool_result_to_dict(write_scoped_result))
            append_tool_category_event(
                tool_category_state_file,
                creation_tool_started_event(tool_name, arguments),
            )

    # Validate db is available
    if db is None:
        result = make_text_result(
            json.dumps({"status": "error", "detail": "Database session not available"}),
            is_error=True,
        )
        return _jsonrpc_result(msg_id, _tool_result_to_dict(result))

    result = await execute_tool(
        db, project_id, tool_name, arguments,
        allowed_tiers=allowed_tiers,
        permission_pack=permission_pack,
        creation_session_id=creation_session_id,
    )
    if tool_category_state_file:
        result_payload = _tool_result_payload(result, tool_name)
        _record_scoped_tool_result(
            tool_category_state_file=tool_category_state_file,
            permission_pack=permission_pack,
            tool_name=tool_name,
            arguments=arguments,
            result_payload=result_payload,
        )
    return _jsonrpc_result(msg_id, _tool_result_to_dict(result))


def _handle_tools_call(
    msg_id: Any,
    params: dict,
    db: Any,
    project_id: str,
    allowed_tiers: set[str],
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
) -> str:
    """Handle tools/call request — sync wrapper.

    When called from serve_stdio (async context), delegates to the async version.
    When db is None (e.g. in tests), returns a sync error response.
    """
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if not isinstance(arguments, dict):
        arguments = {}

    if tool_category_state_file:
        scoped_result = _category_scoped_call_result(
            tool_name,
            arguments,
            tool_category_state_file,
        )
        if scoped_result is not None:
            return _jsonrpc_result(msg_id, _tool_result_to_dict(scoped_result))
        if permission_pack == "creation_session":
            write_scoped_result = _creation_turn_write_scoped_call_result(
                tool_name,
                arguments,
                tool_category_state_file,
            )
            if write_scoped_result is not None:
                return _jsonrpc_result(msg_id, _tool_result_to_dict(write_scoped_result))
            append_tool_category_event(
                tool_category_state_file,
                creation_tool_started_event(tool_name, arguments),
            )

    # If no db session, return error
    if db is None:
        result = make_text_result(
            json.dumps({"status": "error", "detail": "Database session not available for tool execution"}),
            is_error=True,
        )
        return _jsonrpc_result(msg_id, _tool_result_to_dict(result))

    # For sync context, try to run the async executor
    import asyncio
    try:
        result = asyncio.run(execute_tool(
            db, project_id, tool_name, arguments,
            allowed_tiers=allowed_tiers,
            permission_pack=permission_pack,
            creation_session_id=creation_session_id,
        ))
    except RuntimeError:
        # If there's already a running event loop, use nest_asyncio or fallback
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(execute_tool(
            db, project_id, tool_name, arguments,
            allowed_tiers=allowed_tiers,
            permission_pack=permission_pack,
            creation_session_id=creation_session_id,
        ))
    if tool_category_state_file:
        result_payload = _tool_result_payload(result, tool_name)
        _record_scoped_tool_result(
            tool_category_state_file=tool_category_state_file,
            permission_pack=permission_pack,
            tool_name=tool_name,
            arguments=arguments,
            result_payload=result_payload,
        )
    return _jsonrpc_result(msg_id, _tool_result_to_dict(result))


def _tool_result_to_dict(result: McpToolResult) -> dict:
    """Convert McpToolResult to MCP protocol dict."""
    return {
        "content": result.content,
        "isError": result.is_error,
    }


def serve_stdio(
    *,
    db: Any = None,
    project_id: str = "",
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
) -> None:
    """Run the MCP server over stdio (blocking).

    Reads newline-delimited JSON-RPC from stdin, writes responses to stdout.

    Args:
        db: SQLAlchemy session for tool execution.
        project_id: Default project ID.
        allowed_tiers: Permission tiers to allow. Defaults to {"readonly"}.
        permission_pack: Permission pack name. If "auto", resolves from
            global/project settings. If a fixed pack name, uses that directly.
        creation_session_id: Session boundary required by the creation_session pack.
    """
    _configure_stdio_utf8()

    # Resolve "auto" permission pack from settings
    resolved_pack = permission_pack
    if permission_pack == "creation_session" and not creation_session_id:
        raise ValueError("creation_session permission pack requires --creation-session-id")
    if permission_pack == "creation_session":
        if not tool_category_state_file:
            raise ValueError("creation_session permission pack requires --tool-category-state-file")
    if tool_category_state_file:
        read_tool_category_state(tool_category_state_file)
    managed_agent_kind = get_compatible_env("SIMING_MANAGED_AGENT_KIND").strip().lower()
    if managed_agent_kind == "cataloging":
        resolved_pack = "cataloging_worker"
        logger.info("Managed cataloging Agent: using compact MCP permission pack")
    elif permission_pack == "auto" and db is not None:
        try:
            from app.services.external_agent.permissions import resolve_effective_pack
            result = resolve_effective_pack(db, project_id=project_id or None)
            resolved_pack = result["effective_pack"]
            logger.info("Auto-resolved permission pack: %s (source: %s)", resolved_pack, result["source"])
        except Exception as exc:
            logger.warning("Failed to resolve auto permission pack: %s, falling back to readonly", exc)
            resolved_pack = "readonly_collaboration"

    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    stdin: TextIO = sys.stdin
    stdout: TextIO = sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_message(
            line,
            db=db,
            project_id=project_id,
            allowed_tiers=allowed_tiers,
            permission_pack=resolved_pack,
            creation_session_id=creation_session_id,
            tool_category_state_file=tool_category_state_file,
        )
        stdout.write(response + "\n")
        stdout.flush()
