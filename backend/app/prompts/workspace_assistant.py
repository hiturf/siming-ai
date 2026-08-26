"""Prompt templates for the shared workspace assistant — agentic multi-turn variant."""
from __future__ import annotations

MAX_ITERATIONS = 12

def build_workspace_assistant_initial_user_message(
    *,
    project_id: str,
    project_title: str,
    history_text: str,
    explicit_context: list[str],
    outline_batch_count: int,
    user_message: str,
) -> str:
    explicit_context_block = "\n\n".join(explicit_context)
    if explicit_context_block:
        explicit_context_block = f"\n\n【用户明确提供的编辑内容】\n{explicit_context_block}"
    return (
        f"作品：{project_title}（project_id={project_id}）\n\n"
        f"【历史对话 — 仅供参考，不要重复执行历史中的操作】\n{history_text}\n\n"
        f"{explicit_context_block}\n\n"
        f"用户设置：连续规划章数={outline_batch_count}。该数字只控制大纲规划数量，不代表连续生成正文。\n\n"
        f"【当前任务 — 必须执行】\n{user_message}\n\n"
        "重要提醒：你的任务是执行【当前任务】中的最新指令，而不是重复历史对话中的旧操作。"
        "历史对话仅用于理解上下文，不要照搬其中的工具调用。作品资料、章节、角色、立项信息和记忆均需按当前任务主动读取。"
    )


def _compress_search_result(result: dict) -> dict | None:
    """Compress a single search result for persistent context — lightweight fields only."""
    tool = str(result.get("tool") or "")
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None
    compressed = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entry: dict = {}
        for key in ("id", "name", "title", "dimension", "role_type", "outline_node_id", "node_type", "direction", "target_name", "relationship_type"):
            if key in item:
                entry[key] = item[key]
        # Truncate long text fields
        for key in ("content", "summary", "personality", "background", "description"):
            if key in item and item[key]:
                text = str(item[key])
                entry[key] = text[:300] + ("..." if len(text) > 300 else "")
        if "children" in item:
            entry["children_count"] = len(item["children"])
        if not entry:
            continue
        compressed.append(entry)
    if not compressed:
        return None
    return {"tool": tool, "detail": result.get("detail", ""), "data": compressed}


def redact_tool_result_for_model(result: dict) -> dict:
    """Keep tool feedback compact while preserving references the model needs."""
    if not isinstance(result, dict) or result.get("tool") != "chapter_writer":
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    content = str(data.get("content") or "")
    if not content:
        return result
    compact_data = dict(data)
    compact_data["content_preview"] = content[:500] + ("..." if len(content) > 500 else "")
    compact_data.pop("content", None)
    compact_data["usage_note"] = (
        "章节草稿已经写入草稿存储，本轮立即结束。"
        "不得调用正式章节、角色、关系、时间线、世界观或建档写入工具；"
        "作者会在界面选择保存方式。"
    )
    return {**result, "data": compact_data}
