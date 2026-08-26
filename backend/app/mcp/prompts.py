"""MCP prompt definitions for Siming.

Exposes MCP prompts that external clients can use to get structured
writing context, continuity checks, and draft assistance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class McpPromptArg:
    """Argument definition for an MCP prompt."""
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class McpPrompt:
    """MCP Prompt definition."""
    name: str
    description: str
    args: list[McpPromptArg]


@dataclass
class McpPromptMessage:
    """A message in an MCP prompt response."""
    role: str
    content: str


def list_prompts() -> list[McpPrompt]:
    """Return all available MCP prompts."""
    return [
        McpPrompt(
            name="moshu_quickstart",
            description="Explain how an external agent should use Siming safely. Covers project selection, import, API-free cataloging, writing, and verification.",
            args=[
                McpPromptArg(name="task", description="User task or scenario (optional)"),
                McpPromptArg(name="project_id", description="Project ID when known (optional)"),
                McpPromptArg(name="no_api", description="true when Siming internal model API should not be used (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_external_cataloging",
            description="API-free cataloging workflow for Claude Code/Codex. Use when Siming API is unavailable and the external agent must analyze chapters itself.",
            args=[
                McpPromptArg(name="project_id", description="Project ID when known (optional)"),
                McpPromptArg(name="job_id", description="Cataloging job ID when already started (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_writing_context",
            description="Generate a compact writing context prompt for a chapter. "
                        "Contains outline, recent summaries, character states, "
                        "worldbuilding constraints, and risk warnings.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="chapter_number", description="Chapter number (optional)"),
                McpPromptArg(name="outline_node_id", description="Outline node ID (optional)"),
                McpPromptArg(name="requirements", description="Writing requirements or direction (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_continuity_check",
            description="Generate a continuity check prompt for OOC and setting-conflict review. "
                        "Contains character states and worldbuilding constraints.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="chapter_id", description="Chapter ID to check (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_fanfic_draft",
            description="Generate a fanfic draft prompt with anti-OOC and no-secret rules. "
                        "For external AI clients writing derivative chapters.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="outline_node_id", description="Outline node ID (optional)"),
                McpPromptArg(name="requirements", description="Fanfic requirements (optional)"),
            ],
        ),
    ]


def get_prompt(name: str) -> McpPrompt | None:
    """Look up a prompt by name."""
    for p in list_prompts():
        if p.name == name:
            return p
    return None


def render_quickstart(
    db: Any,
    *,
    task: str | None = None,
    project_id: str | None = None,
    no_api: str | None = None,
) -> list[McpPromptMessage]:
    """Render a project-optional quickstart prompt."""
    no_api_flag = str(no_api or "").lower() in {"1", "true", "yes", "y", "是"}
    parts = [
        "# Siming / 司命外部 Agent 快速入口",
        "",
        "## 必读规则",
        "- 默认使用 API-free 外部流程：除非用户明确说“使用司命内部 API/内部模型/系统模型额度”，不要调用内部模型工具。",
        "- 内部模型工具只通过 MCP permission pack: internal_llm 暴露；project_management 只用于 API-free 的项目创建、导入、写入、导出、技能和自动任务管理。",
        "- 中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为工具报错就改成英文或拼音。",
        "- 先调用 list_projects 或 get_project_info 确认作品；所有项目写入工具都必须传入正确 project_id。",
        "- 创建、导入和建档后必须调用 get_project_archive_status 或对应 search/list 工具验证数据。章节写作只生成未保存草稿，草稿成功后必须立即停止。",
        "- 禁止默认调用的内部模型工具：chapter_writer, character_writer, outline_writer, worldbuilding_writer, design_plot, roleplay_character, dialogue_battle, evaluate_chapter, detect_character_changes, detect_new_worldbuilding, detect_worldbuilding_conflicts, rewrite_text, expand_text, continue_text, start_cataloging_job, resume_cataloging_job, retry_current_cataloging_chapter, rerun_cataloging_resolution_current, start_deconstruct_job。",
        "",
        "## 默认外部建档流程",
        "get_prompt_pack(pack_id='cataloging_external_no_api') -> start_external_cataloging_job -> 逐章执行 facts / candidates / apply / verify -> get_project_archive_status。",
        "",
        "## 默认外部写作流程",
        "prepare_external_writing_context -> 外部 Agent 一次生成正文 -> save_external_chapter_draft -> 立即停止。不得继续写入正式章节、角色/世界观或调用建档工具；作者会在界面选择“保存并建档”或“仅保存”。去除 AI 味和质量评分读取编辑器当前草稿，由用户另行发起。",
        "",
        "# Siming / 司命外部 Agent 快速入门",
        "",
        "你正在通过 MCP 操作司命。不要把工具列表当成普通 CRUD 猜着用，先根据任务选择工作流。",
        "",
        "## 通用规则",
        "- 第一步通常调用 get_moshu_usage_guide；不确定时 scenario=quickstart。",
        "- 中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为一次工具错误就改成英文或拼音。",
        "- 先调用 list_projects 或 get_project_info 确认作品；所有项目写入都必须使用正确 project_id。",
        "- 完成导入或建档后，必须调用 get_project_archive_status 或 search/list 工具验证数据。章节草稿写入成功即结束，不执行后续验证轮询。",
        "- 如果用户说司命 API 欠费、未配置 API、或要求由 Claude/Codex 自己分析，禁止调用内部 LLM 工具。",
        "- 内部 LLM 工具包括 start_cataloging_job、chapter_writer、character_writer、outline_writer、worldbuilding_writer、design_plot、evaluate_chapter。",
        "",
        "## 导入本地小说",
        "1. import_file_as_project(file_path, title)",
        "2. get_project_archive_status() 验证 chapters_count",
        "3. 需要建档时继续无 API 建档或内部建档",
        "",
        "## 无 API 建档",
        "1. get_prompt_pack(pack_id='cataloging_external_no_api')",
        "2. start_external_cataloging_job()",
        "3. get_next_external_cataloging_chapter(phase='facts') -> 只读当前章 -> save_external_cataloging_facts",
        "4. get_next_external_cataloging_chapter(phase='candidates') -> list_cataloging_facts -> 读取当前档案 -> save_external_cataloging_candidates",
        "5. apply_pending_cataloging -> verify_external_cataloging_progress；逐章重复，最后 get_project_archive_status",
        "",
        "## 无 API 写章节",
        "1. prepare_external_writing_context()",
        "2. 外部 Agent 一次生成基础正文；不自动执行去除 AI 味或质量评审",
        "3. 调用 save_external_chapter_draft 保存未入库草稿，然后立即停止；正式保存和启动建档只能由作者在界面操作",
    ]
    from app.prompts.cataloging_source import get_language_rules, get_project_binding_rules

    parts.extend([
        "",
        "## Context Governance (Required for Agent Tasks)",
        "- Before writing, review, rewriting, or cataloging a concrete chapter, call prepare_task_context to obtain the baseline manifest.",
        "- Use search_task_context for focused follow-up retrieval. Reading a project mirror directly remains allowed but is not auditable evidence.",
        "- Before saving a generated draft, call submit_context_evidence for every required manifest item using its source hash.",
        "- Pass context_manifest_id through prepare_external_writing_context and save_external_chapter_draft.",
        "",
        get_project_binding_rules(),
        "",
        get_language_rules(),
    ])
    if task:
        parts.append(f"\n## 当前任务\n{task}")
    if project_id:
        parts.append(f"\n## 当前 project_id\n{project_id}")
    if no_api_flag:
        parts.append("\n## 当前限制\n用户要求不使用司命内部 API。请走 external/no-api 工具链。")
    return [McpPromptMessage(role="user", content="\n".join(parts))]


def render_external_cataloging(
    db: Any,
    *,
    project_id: str | None = None,
    job_id: str | None = None,
) -> list[McpPromptMessage]:
    """Render the API-free external cataloging prompt."""
    from app.prompts.cataloging_source import get_external_cataloging_system_prompt

    parts = [get_external_cataloging_system_prompt()]
    if project_id:
        parts.append(f"\n## project_id\n{project_id}")
    if job_id:
        parts.append(f"\n## job_id\n{job_id}")
    return [McpPromptMessage(role="user", content="\n".join(parts))]


def _render_governed_task_prompt(
    db: Any,
    *,
    project_id: str,
    task_type: str,
    title: str,
    arguments: dict[str, Any],
) -> list[McpPromptMessage]:
    """Render the persisted context manifest shared by every execution route."""
    from app.database.models import Project
    from app.services.context_orchestrator import ContextOrchestrator

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.prepare(
        project_id=project_id,
        task_type=task_type,
        execution_route="external_mcp",
        arguments=arguments,
    )
    if not isinstance(manifest.id, str) or not manifest.id:
        return [McpPromptMessage(role="user", content="Error: a persisted context manifest could not be prepared.")]
    payload = orchestrator.manifest_payload(manifest, include_content=True)
    state = payload["status"]
    workflow = (
        "1. Call prepare_task_context with this manifest_id or your run_id.\n"
        "2. Use search_task_context only for a focused gap.\n"
        "3. Before save_external_chapter_draft, call submit_context_evidence for every required source.\n"
        "4. After save_external_chapter_draft succeeds, stop immediately; the author owns formal saving and cataloging.\n"
        "5. Direct project-mirror reads may inform exploration, but are not verified evidence."
    )
    parts = [
        f"# Siming Governed Context: {title}",
        f"Project: {project.title}",
        f"context_manifest_id: {manifest.id}",
        f"manifest_status: {state}",
        f"input_budget: {payload['budget']['estimated_input_tokens']}/{payload['budget']['input_budget_tokens']} tokens",
        "\n## Required MCP Workflow\n" + workflow,
    ]
    if state != "ready":
        parts.append("\n## Author Confirmation Required\n" + "\n".join(payload.get("warnings") or ["Required context is unavailable."]))
    parts.append("\n## Governed Task Context\n" + (payload.get("rendered_context") or "No context could be rendered."))
    return [McpPromptMessage(role="user", content="\n".join(parts))]


def render_writing_context(
    db: Any,
    project_id: str,
    *,
    chapter_number: str | None = None,
    outline_node_id: str | None = None,
    requirements: str | None = None,
) -> list[McpPromptMessage]:
    return _render_governed_task_prompt(
        db,
        project_id=project_id,
        task_type="writing",
        title="Writing",
        arguments={
            "chapter_number": chapter_number or "",
            "outline_node_id": outline_node_id or "",
            "requirements": requirements or "",
        },
    )


def render_continuity_check(
    db: Any,
    project_id: str,
    *,
    chapter_id: str | None = None,
) -> list[McpPromptMessage]:
    return _render_governed_task_prompt(
        db,
        project_id=project_id,
        task_type="review",
        title="Continuity Review",
        arguments={"chapter_id": chapter_id or ""},
    )


def render_fanfic_draft(
    db: Any,
    project_id: str,
    *,
    outline_node_id: str | None = None,
    requirements: str | None = None,
) -> list[McpPromptMessage]:
    return _render_governed_task_prompt(
        db,
        project_id=project_id,
        task_type="writing",
        title="Fanfic Draft",
        arguments={
            "outline_node_id": outline_node_id or "",
            "requirements": requirements or "",
        },
    )


def render_prompt(
    db: Any,
    name: str,
    arguments: dict[str, str],
) -> list[McpPromptMessage] | None:
    """Dispatch prompt rendering by name.

    Returns None if the prompt name is unknown.
    """
    if name == "moshu_quickstart":
        return render_quickstart(
            db,
            task=arguments.get("task"),
            project_id=arguments.get("project_id"),
            no_api=arguments.get("no_api"),
        )
    if name == "moshu_external_cataloging":
        return render_external_cataloging(
            db,
            project_id=arguments.get("project_id"),
            job_id=arguments.get("job_id"),
        )

    project_id = arguments.get("project_id", "")
    if not project_id:
        return [McpPromptMessage(role="user", content="Error: project_id is required.")]

    if name == "moshu_writing_context":
        return render_writing_context(
            db, project_id,
            chapter_number=arguments.get("chapter_number"),
            outline_node_id=arguments.get("outline_node_id"),
            requirements=arguments.get("requirements"),
        )
    elif name == "moshu_continuity_check":
        return render_continuity_check(
            db, project_id,
            chapter_id=arguments.get("chapter_id"),
        )
    elif name == "moshu_fanfic_draft":
        return render_fanfic_draft(
            db, project_id,
            outline_node_id=arguments.get("outline_node_id"),
            requirements=arguments.get("requirements"),
        )
    return None
