"""Resolve the Siming stdio MCP command without touching client config."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.services.application_settings import app_home


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def resolve_siming_mcp_server(
    *,
    permission_pack: str,
    project_id: str = "",
    creation_session_id: str = "",
    tool_category_state_file: str = "",
) -> dict[str, Any]:
    """Return an executable MCP spec for persistent or process-scoped clients."""

    scope_args = ["--permission-pack", permission_pack]
    if project_id:
        scope_args.extend(["--project-id", project_id])
    if creation_session_id:
        scope_args.extend(["--creation-session-id", creation_session_id])
    if tool_category_state_file:
        scope_args.extend(["--tool-category-state-file", tool_category_state_file])

    if getattr(sys, "frozen", False):
        return {
            "mode": "exe",
            "command": str(Path(sys.executable).resolve()),
            "args": ["--mcp-server", *scope_args],
            # Never inherit another Agent's working directory. This also
            # prevents foreign dotenv/project configuration from leaking in.
            "cwd": str(app_home().resolve()),
        }

    root = _repo_root()
    entry = root / "scripts" / "moshu-mcp-server.py"
    if entry.exists():
        return {
            "mode": "source",
            "command": str(Path(sys.executable).resolve()),
            "args": [str(entry.resolve()), *scope_args],
            "cwd": str(root),
        }

    return {
        "mode": "python_module",
        "command": str(Path(sys.executable).resolve()),
        "args": ["-m", "app.mcp.server", *scope_args],
        "cwd": str(root),
    }


__all__ = ["resolve_siming_mcp_server"]
