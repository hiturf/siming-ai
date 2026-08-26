# ruff: noqa: E501
"""Model-runtime workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="start_local_cli_agent_run",
        description="Start a Siming-managed local CLI Agent worker for general project work or cataloging only. Chapter writing uses the assistant's direct API/CLI draft path. Never call this from an already-running external MCP client.",
        input_schema={
            "task_type": {
                "type": "string",
                "enum": ["general", "cataloging"],
                "description": "general|cataloging",
            },
            "user_request": {
                "type": "string",
                "description": "User request for the local CLI agent",
            },
            "provider": {
                "type": "string",
                "description": "Optional local CLI provider id, e.g. claude_cli/codex_cli/opencode_cli/mimocode_cli/cursor_cli/kilocode_cli/qwen_code_cli/hermes_cli/openclaw_cli/dsh_cli",
            },
            "chapter_id": {
                "type": "string",
                "description": "Cataloging/review target chapter for the governed baseline",
            },
            "context_manifest_id": {
                "type": "string",
                "description": "Optional previously prepared baseline manifest",
            },
            "pinned_chunk_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Author-pinned context chunks",
            },
            "pinned_source_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Author-pinned context source ids",
            },
        },
        tool_type="scheduler",
        estimated_cost="local_cli",
        handler_name="start_local_cli_agent_run",
    ),
    ToolDef(
        name="wait_local_cli_agent_run",
        description="Wait for a Siming-managed general or cataloging CLI Agent run to finish.",
        input_schema={
            "run_id": {
                "type": "string",
                "description": "Agent run ID returned by start_local_cli_agent_run",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Maximum wait time; default 1800",
            },
            "startup_timeout_seconds": {
                "type": "integer",
                "description": "Maximum time to wait for cli_started; default 10",
            },
            "poll_seconds": {"type": "number", "description": "Polling interval; default 2"},
        },
        required=["run_id"],
        tool_type="scheduler",
        estimated_cost="free",
        handler_name="wait_local_cli_agent_run",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
