"""Deterministic Windows user PATH integration helpers."""
from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any


def path_integration_supported() -> bool:
    return os.name == "nt"


def _expand_environment_variables(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return os.environ.get(name, os.environ.get(name.upper(), match.group(0)))

    return re.sub(r"%([^%]+)%", replace, value)


def normalize_windows_path_entry(value: str) -> str:
    cleaned = str(value or "").strip().strip('"')
    if not cleaned:
        return ""
    expanded = _expand_environment_variables(cleaned)
    return ntpath.normcase(ntpath.normpath(expanded)).rstrip("\\/")


def windows_path_contains(value: str, target: str) -> bool:
    normalized_target = normalize_windows_path_entry(target)
    return bool(normalized_target) and any(
        normalize_windows_path_entry(entry) == normalized_target
        for entry in str(value or "").split(";")
    )


def updated_windows_path(value: str, target: str, *, enabled: bool) -> str:
    raw = str(value or "")
    if enabled:
        if windows_path_contains(raw, target):
            return raw
        return f"{raw.rstrip(';')};{target}" if raw.rstrip(";") else target

    normalized_target = normalize_windows_path_entry(target)
    return ";".join(
        entry
        for entry in raw.split(";")
        if normalize_windows_path_entry(entry) != normalized_target
    )


def read_current_user_path() -> tuple[str, int]:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            try:
                value, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return "", winreg.REG_EXPAND_SZ
    except FileNotFoundError:
        return "", winreg.REG_EXPAND_SZ
    return str(value or ""), int(value_type)


def write_current_user_path(value: str, value_type: int) -> None:
    import winreg

    allowed_types = {winreg.REG_SZ, winreg.REG_EXPAND_SZ}
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(
            key,
            "Path",
            0,
            value_type if value_type in allowed_types else winreg.REG_EXPAND_SZ,
            value,
        )


def broadcast_environment_change() -> None:
    if not path_integration_supported():
        return
    try:
        import ctypes

        result = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,
            0x001A,
            0,
            "Environment",
            0x0002,
            2000,
            ctypes.byref(result),
        )
    except (AttributeError, OSError, TypeError):
        return


def user_path_status(
    command: str | None,
    managed_command: Path,
    *,
    supported: bool,
    read_path: Callable[[], tuple[str, int]],
) -> dict[str, Any]:
    directory = str(managed_command.parent)
    user_path = ""
    if supported:
        try:
            user_path, _value_type = read_path()
        except (ImportError, OSError):
            user_path = ""
    return {
        "supported": supported,
        "managed_install": bool(
            command
            and normalize_windows_path_entry(command)
            == normalize_windows_path_entry(str(managed_command))
        ),
        "configured": supported and windows_path_contains(user_path, directory),
        "directory": directory,
        "scope": "user",
        "requires_new_terminal": supported,
    }


def configure_user_path(
    managed_command: Path,
    *,
    enabled: bool,
    supported: bool,
    read_path: Callable[[], tuple[str, int]],
    write_path: Callable[[str, int], None],
    broadcast: Callable[[], None],
) -> dict[str, bool]:
    if not supported:
        raise RuntimeError("当前系统不支持自动配置 Windows 用户 PATH")
    if enabled and not managed_command.is_file():
        raise RuntimeError("司命托管的 OpenCode 尚未安装，无法添加到 PATH")

    target = str(managed_command.parent)
    try:
        user_path, value_type = read_path()
        next_user_path = updated_windows_path(user_path, target, enabled=enabled)
        registry_changed = next_user_path != user_path
        if registry_changed:
            write_path(next_user_path, value_type)
            verified_path, _verified_type = read_path()
            if windows_path_contains(verified_path, target) != enabled:
                raise RuntimeError("Windows 用户 PATH 写入后校验失败")
    except (ImportError, OSError) as exc:
        raise RuntimeError(f"无法修改当前用户 PATH：{exc}") from exc

    process_path = os.environ.get("PATH", "")
    next_process_path = updated_windows_path(process_path, target, enabled=enabled)
    process_changed = next_process_path != process_path
    if process_changed:
        os.environ["PATH"] = next_process_path
    if registry_changed:
        broadcast()
    return {
        "registry_changed": registry_changed,
        "process_changed": process_changed,
    }
