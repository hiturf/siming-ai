"""Safe, user-facing progress projection for creation Agent tools."""

from __future__ import annotations

from typing import Any

from app.architecture.tool_categories import TOOL_CATEGORY_CONTROLLER

_TOOL_LABELS: dict[str, str] = {
    "get_creation_snapshot": "读取当前立项快照",
    "get_creation_session": "读取立项会话",
    "get_creation_operation": "查看后台任务",
    "get_creation_artifact": "读取阶段资料",
    "list_creation_artifacts": "检查全部阶段资料",
    "get_creation_dependencies": "检查资料依赖",
    "get_creation_dependency_graph": "读取依赖关系图",
    "validate_creation_consistency": "检查资料一致性",
    "validate_creation_session": "校验立项完整性",
    "patch_creation_session": "更新立项基本资料",
    "patch_creation_artifact": "更新阶段资料",
    "lock_creation_fields": "锁定作者确认字段",
    "unlock_creation_fields": "解除字段锁定",
    "undo_creation_artifact": "撤销阶段修改",
    "list_creation_entities": "查询实体列表",
    "get_creation_entity": "读取目标实体",
    "patch_creation_entity": "更新目标实体",
    "delete_creation_entity": "删除目标实体",
    "list_creation_artifact_versions": "读取阶段版本",
    "get_creation_artifact_diff": "比较阶段版本",
    "restore_creation_artifact_version": "恢复阶段版本",
    "confirm_creation_artifact": "确认阶段资料",
    "generate_creation_artifact": "生成阶段资料",
    "refine_creation_artifact": "优化阶段资料",
    "regenerate_creation_artifact": "重新生成阶段资料",
    "finalize_creation_session": "创建正式作品",
    "cancel_creation_operation": "取消后台任务",
    "pause_creation_operation": "暂停后台任务",
    "resume_creation_operation": "继续后台任务",
    "retry_creation_operation": "重试后台任务",
    "preview_creation_import": "预览资料导入",
    "apply_creation_import": "应用资料导入",
    "mcp_verified_write": "验证本机工具写入",
}


def creation_tool_label(tool_name: str) -> str:
    return _TOOL_LABELS.get(tool_name, "处理立项资料")


def _short_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _safe_target(arguments: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, str]:
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    target_type = _short_text(
        arguments.get("entity_type")
        or arguments.get("artifact")
        or data.get("entity_type")
        or data.get("artifact"),
        48,
    )
    target_name = _short_text(
        data.get("name")
        or data.get("title")
        or arguments.get("name")
        or arguments.get("title"),
        80,
    )
    safe: dict[str, str] = {}
    if target_type:
        safe["affected_type"] = target_type
    if target_name:
        safe["affected_name"] = target_name
    return safe


def creation_tool_started_event(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    label = creation_tool_label(tool_name)
    return {
        "type": "tool_started",
        "message": f"正在{label}…",
        "data": {
            "tool": tool_name,
            "label": label,
            "status": "running",
            **_safe_target(arguments),
        },
    }


def creation_tool_completed_event(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    label = creation_tool_label(tool_name)
    status = str(result.get("status") or "ok")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    revision_before = arguments.get("expected_revision")
    revision_after = data.get("revision_after", data.get("revision"))
    safe_data: dict[str, Any] = {
        "tool": tool_name,
        "label": label,
        "status": status,
        **_safe_target(arguments, result),
    }
    if isinstance(revision_before, int):
        safe_data["revision_before"] = revision_before
    if isinstance(revision_after, int):
        safe_data["revision_after"] = revision_after

    if status == "ok":
        message = f"已完成：{label}"
    elif status == "running":
        message = f"已启动：{label}"
    elif status == "skipped":
        message = f"已跳过：{label}"
    else:
        message = f"未完成：{label}"
    return {"type": "tool_completed", "message": message, "data": safe_data}


def is_creation_control_tool(tool_name: str) -> bool:
    return tool_name == TOOL_CATEGORY_CONTROLLER


__all__ = [
    "creation_tool_completed_event",
    "creation_tool_label",
    "creation_tool_started_event",
    "is_creation_control_tool",
]
