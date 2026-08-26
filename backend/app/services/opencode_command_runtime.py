"""OpenCode executable discovery and bounded process probes."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.ai.local_cli_adapter import (
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_RETIRED_MODELS,
    hidden_subprocess_kwargs,
)


def resolve_candidate(candidate: str | None) -> str | None:
    value = str(candidate or "").strip().strip('"')
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return str(path.resolve())
    resolved = shutil.which(value)
    return str(Path(resolved).resolve()) if resolved else None


def resolve_command(preferred: str | None, managed_command: Path) -> str | None:
    candidates = [
        preferred,
        str(managed_command),
        "opencode.cmd",
        "opencode.exe",
        "opencode",
    ]
    return next((resolved for item in candidates if (resolved := resolve_candidate(item))), None)


def subprocess_command(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def command_version(command: str, *, timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(
            subprocess_command(command, ["--version"]),
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or result.stderr or "").strip().splitlines()
    return value[0][:100] if value else None


def is_free_model(model_id: str) -> bool:
    normalized = str(model_id or "").strip().lower()
    if normalized in OPENCODE_RETIRED_MODELS:
        return False
    return normalized.endswith("-free") or normalized == "opencode/big-pickle"


def free_model_options(models: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "id": model_id,
            "display_name": str(item.get("display_name") or model_id),
            "recommended": model_id == OPENCODE_DEFAULT_MODEL,
        }
        for item in models
        if (model_id := str(item.get("id") or "").strip()) and is_free_model(model_id)
    ]
