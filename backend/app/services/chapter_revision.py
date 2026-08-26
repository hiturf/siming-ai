"""Non-destructive chapter revision previews."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from ..ai.local_cli_adapter import is_local_cli_provider
from ..core.exceptions import LLMError, NotFoundError, ValidationError
from ..core.utils import count_words
from ..database.models import Chapter, Project
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..prompts.anti_ai_prompts import (
    apply_de_ai_macro_ledger,
    build_de_ai_chunk_repair_prompt,
    build_de_ai_chunked_rewrite_prompts,
    build_de_ai_fidelity_audit_prompt,
    build_de_ai_macro_ledger_compression_prompt,
    build_de_ai_macro_ledger_retry_feedback,
    build_de_ai_story_ledger_prompt,
    build_de_ai_style_audit_prompt,
    build_de_ai_style_repair_prompt,
    normalize_de_ai_macro_ledger,
    validate_de_ai_macro_ledger,
)
from ..prompts.style_prompts import build_style_context
from .de_ai_validation import (
    DE_AI_STRUCTURAL_OUTPUT_ATTEMPTS,
    DE_AI_STRUCTURAL_REPAIR_ATTEMPTS,
    assess_de_ai_revision,
    count_de_ai_visible_characters,
    de_ai_chunk_length_rank,
    de_ai_style_issue_rank,
    is_de_ai_structural_branch_repairable,
    parse_de_ai_chunk_target,
    parse_de_ai_fidelity_audit,
    parse_de_ai_style_audit,
)


_DE_AI_API_LEDGER_TIMEOUT_SECONDS = 180
_DE_AI_API_GENERATION_TIMEOUT_SECONDS = 240
_DE_AI_LOCAL_CLI_GENERATION_TIMEOUT_SECONDS = 600
_DE_AI_API_OPTIONAL_LENGTH_RETRY_SECONDS = 90.0
_DE_AI_LOCAL_CLI_OPTIONAL_LENGTH_RETRY_SECONDS = 600.0
_DE_AI_API_REVIEW_TIMEOUT_SECONDS = 180.0
_DE_AI_LOCAL_CLI_REVIEW_TIMEOUT_SECONDS = 900.0


def _clean_revision_output(value: Any) -> str:
    """Remove a surrounding Markdown fence without changing the prose itself."""
    text = str(value or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _audit_runtime_failure(label: str, exc: Exception) -> dict[str, Any]:
    """Represent an unavailable audit without discarding a generated candidate."""

    detail = str(exc).strip() or exc.__class__.__name__
    return {
        "valid": False,
        "passed": False,
        "issues": [{
            "chunk": 0,
            "kind": "audit_unavailable",
            "detail": f"{label}未能完成：{detail}",
        }],
        "exhausted": True,
    }


def _mark_audit_exhausted(
    audit: dict[str, Any],
    *,
    detail: str | None = None,
    repair_attempts: int = 0,
) -> dict[str, Any]:
    issues = [dict(item) for item in audit.get("issues", []) if isinstance(item, dict)]
    if detail:
        issues.append({
            "chunk": 0,
            "kind": "repair_unavailable",
            "detail": detail,
        })
    return {
        **audit,
        "passed": False,
        "issues": issues,
        "exhausted": True,
        "repair_attempts": repair_attempts,
    }


def _revision_preview_warnings(
    assessment: dict[str, Any],
    fidelity_audit: dict[str, Any],
    style_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flatten all review diagnostics into stable, UI-friendly warnings."""

    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def append_issue(source: str, issue: dict[str, Any]) -> None:
        detail = str(issue.get("detail") or "").strip()
        if not detail:
            return
        code = str(issue.get("code") or issue.get("kind") or "review_warning")
        try:
            chunk = int(issue.get("chunk") or 0)
        except (TypeError, ValueError):
            chunk = 0
        identity = (source, code, detail, chunk)
        if identity in seen:
            return
        seen.add(identity)
        warning: dict[str, Any] = {
            "source": source,
            "code": code,
            "detail": detail,
        }
        if chunk > 0:
            warning["chunk"] = chunk
        warnings.append(warning)

    for issue in assessment.get("issues", []):
        if isinstance(issue, dict):
            append_issue("revision_quality", issue)
    for source, audit in (
        ("fidelity_audit", fidelity_audit),
        ("style_audit", style_audit),
    ):
        if bool(audit.get("valid")) and bool(audit.get("passed")):
            continue
        for issue in audit.get("issues", []):
            if isinstance(issue, dict):
                append_issue(source, issue)
        if not any(item[0] == source for item in seen):
            append_issue(source, {
                "kind": "review_warning",
                "detail": "该项系统审核未通过，请人工对照原文确认。",
            })
    return warnings


def _load_revision_context(
    db: Session,
    project_id: str,
    chapter_id: str | None,
    *,
    content: str,
    original_content: str | None,
    revision_round: int,
) -> tuple[Project, Chapter | None, str, str, int]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    chapter = (
        db.query(Chapter)
        .filter(Chapter.id == chapter_id, Chapter.project_id == project_id)
        .first()
    ) if chapter_id else None
    if chapter_id and not chapter:
        raise NotFoundError("章节不存在")

    source = str(content or "")
    try:
        round_number = int(revision_round)
    except (TypeError, ValueError) as exc:
        raise ValidationError("去除 AI 味轮次必须是 1 到 3") from exc
    if not 1 <= round_number <= 3:
        raise ValidationError("去除 AI 味最多允许处理 3 轮")
    if round_number > 1 and original_content is None:
        raise ValidationError("第 2/3 轮必须提供最初原文，防止连续处理造成故事漂移")
    original = str(original_content if original_content is not None else source)
    if len(source.strip()) < 20 or len(original.strip()) < 20:
        raise ValidationError("正文太短，至少需要 20 个字符才能去除 AI 味")
    return project, chapter, source, original, round_number


def _build_revision_preview(
    *,
    chapter: Chapter | None,
    original: str,
    source: str,
    rewritten: str,
    result: dict[str, Any],
    model: str | None,
    round_number: int,
    fidelity_audit: dict[str, Any],
    style_audit: dict[str, Any],
) -> dict[str, Any]:
    # The immutable original owns story/length safety.  A second assessment
    # against this round's actual input only contributes transform-specific
    # failures (most importantly a near-verbatim no-op).
    assessment = assess_de_ai_revision(
        original,
        rewritten,
        require_substantial_revision=False,
    )
    input_assessment = assess_de_ai_revision(source, rewritten)
    for issue in input_assessment.get("issues", []):
        if not isinstance(issue, dict):
            continue
        # Candidate-only facts never become lineage requirements.  The current
        # input is consulted solely to reject a no-op round.
        if str(issue.get("code") or "") == "insufficient_revision":
            assessment["issues"].append(dict(issue))
    assessment["accepted"] = not assessment.get("issues")
    assessment["input_source_similarity"] = input_assessment.get("source_similarity")
    assessment["input_visible_characters"] = input_assessment.get(
        "original_visible_characters"
    )
    warnings = _revision_preview_warnings(assessment, fidelity_audit, style_audit)
    audit_passed = not warnings
    request_meta = (
        result.get("request_meta")
        if isinstance(result.get("request_meta"), dict)
        else {}
    )
    return {
        "chapter_id": chapter.id if chapter else None,
        "original": original,
        "input": source,
        "rewritten": rewritten,
        "original_word_count": count_words(original),
        "input_word_count": count_words(source),
        "rewritten_word_count": count_words(rewritten),
        "provider": str(request_meta.get("provider") or ""),
        "model": str(request_meta.get("model") or result.get("model") or model or ""),
        "mutated": False,
        "persisted": False,
        "auto_adopted": False,
        "review_required": True,
        "revision_round": round_number,
        "max_revision_rounds": 3,
        "can_continue": round_number < 3,
        "audit_passed": audit_passed,
        "candidate_status": "ready" if audit_passed else "review_with_warnings",
        "warnings": warnings,
        "revision_quality": assessment,
        "fidelity_audit": fidelity_audit,
        "style_audit": style_audit,
    }


async def preview_de_ai_revision(
    db: Session,
    project_id: str,
    chapter_id: str | None,
    *,
    content: str,
    original_content: str | None = None,
    revision_round: int = 1,
    model: str | None = None,
) -> dict[str, Any]:
    """Return one de-AI round without mutating the chapter or its snapshots.

    ``content`` is the text transformed in this round.  Follow-up rounds pass
    the previous system candidate there, while ``original_content`` remains the
    immutable first-round source used by every fidelity and length guard.
    """
    project, chapter, source, original, round_number = _load_revision_context(
        db,
        project_id,
        chapter_id,
        content=content,
        original_content=original_content,
        revision_round=revision_round,
    )

    # This is the explicit de-AI action, so the project and system de-AI
    # constraints belong here rather than in ordinary chapter generation.
    # Keep project voice/perspective, but do not prepend the legacy global
    # ban-list bundle here.  The input-aware rewrite prompt below owns this
    # explicit operation; duplicating pages of negative examples encourages
    # literal copy-editing and primes the very stock phrases we want removed.
    style_context = build_style_context(project, include_anti_ai=False, concise=True)
    ledger_messages = [
        {
            "role": "system",
            "content": (
                "你是小说事实记录员。只抽取原文已经存在的事实、事件顺序、线索和对白意图，"
                "不得续写、解释、评价或添加细节。输出紧凑的事件账本，不输出小说正文。"
            ),
        },
        # Rebuild the ledger from the immutable original on every round.  The
        # previous candidate guides expression diagnostics below, but can never
        # become the authority for story facts.
        {"role": "user", "content": build_de_ai_story_ledger_prompt(original)},
    ]
    rewrite_system_message = {
        "role": "system",
        "content": (
            "你是中文小说的重写编辑。给定内容是整章事实账本中的一个连续片段。"
            "只能重写表达，不规划剧情，不新增或删改事实、人物、设定、事件、线索或关系。"
            "直接输出该片段的小说正文，不要标题、说明、清单、Markdown 或修改报告。\n\n"
            f"【作品文风】\n{style_context}"
        ),
    }
    ledger_max_tokens = min(8_000, max(2_500, int(len(original) * 1.1)))
    request_body: dict[str, Any] = {
        "moshu_task_type": "writing",
        "moshu_project_id": project_id,
    }
    provider_is_local_cli = is_local_cli_provider(
        LLMGateway.provider_for_model(model)
    )
    if provider_is_local_cli:
        # A button-triggered text transform receives the text explicitly. It
        # needs neither filesystem access nor MCP write permission.
        request_body.update({
            "local_cli_isolated": True,
            "local_cli_timeout_seconds": 600,
        })
    extra_body = LLMGateway.local_cli_extra_body(model, base=request_body)
    try:
        ledger_result = await LLMGateway.chat_completion(
            messages=ledger_messages,
            model=model,
            temperature=0.1,
            max_tokens=ledger_max_tokens,
            timeout=(
                _DE_AI_LOCAL_CLI_GENERATION_TIMEOUT_SECONDS
                if provider_is_local_cli
                else _DE_AI_API_LEDGER_TIMEOUT_SECONDS
            ),
            retry=1,
            extra_body=extra_body,
        )
        story_ledger = _clean_revision_output(ledger_result.get("content"))
        minimum_ledger_length = min(80, max(20, len(original) // 4))
        if len(story_ledger) < minimum_ledger_length:
            raise LLMError("去除 AI 味失败：模型没有生成可用的故事账本")
        chunk_prompts = build_de_ai_chunked_rewrite_prompts(
            source,
            story_ledger,
            fidelity_source=original,
        )
        if not chunk_prompts:
            raise LLMError("去除 AI 味失败：故事账本无法切分为连续场景")
        detailed_chunk_prompts = list(chunk_prompts)
        if count_de_ai_visible_characters(original) >= 1_500:
            compact_semaphore = asyncio.Semaphore(
                1 if str(model or "").startswith("opencode_cli:") else 2
            )

            async def compact_chunk_prompt(chunk_prompt: str) -> str:
                async with compact_semaphore:
                    compact_prompt = build_de_ai_macro_ledger_compression_prompt(
                        chunk_prompt
                    )
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "你是小说事实账本压缩员。把详细事件合并成少量宏观叙事单元，"
                                "保留人物、物件归属、数字、条件、因果、对白信息和先后。"
                                "只输出用户要求的短账本，不写小说正文。"
                            ),
                        },
                        {"role": "user", "content": compact_prompt},
                    ]
                    try:
                        compact_ledger = ""
                        valid_compact = False
                        for compact_attempt in range(2):
                            compact_result = await LLMGateway.chat_completion(
                                messages=messages,
                                model=model,
                                temperature=0,
                                max_tokens=1_500,
                                timeout=(
                                    _DE_AI_LOCAL_CLI_GENERATION_TIMEOUT_SECONDS
                                    if provider_is_local_cli
                                    else _DE_AI_API_LEDGER_TIMEOUT_SECONDS
                                ),
                                retry=1,
                                extra_body=extra_body,
                            )
                            compact_ledger = normalize_de_ai_macro_ledger(
                                _clean_revision_output(compact_result.get("content"))
                            )
                            valid_compact, missing = validate_de_ai_macro_ledger(
                                chunk_prompt,
                                compact_ledger,
                            )
                            if valid_compact:
                                break
                            if compact_attempt == 0:
                                messages = [
                                    *messages,
                                    {"role": "assistant", "content": compact_ledger},
                                    {
                                        "role": "user",
                                        "content": build_de_ai_macro_ledger_retry_feedback(
                                            chunk_prompt,
                                            compact_ledger,
                                            missing,
                                        ),
                                    },
                                ]
                    except Exception:
                        # Compression improves granularity but is not allowed
                        # to hide an otherwise usable preview.
                        return chunk_prompt
                if not valid_compact:
                    return chunk_prompt
                # The macro ledger shapes prose granularity; it is not the
                # story-safety authority.  One deterministic validation here
                # keeps preview latency bounded, while the assembled candidate
                # is still audited and repaired against the immutable source.
                return apply_de_ai_macro_ledger(chunk_prompt, compact_ledger)

            chunk_prompts = list(await asyncio.gather(*(
                compact_chunk_prompt(chunk_prompt)
                for chunk_prompt in chunk_prompts
            )))
        model_name = str(model or "")
        if model_name.startswith("opencode_cli:"):
            concurrency_limit = 1
        elif model_name.startswith("codex_cli:"):
            concurrency_limit = 2
        else:
            concurrency_limit = 3
        semaphore = asyncio.Semaphore(min(concurrency_limit, len(chunk_prompts)))
        event_loop = asyncio.get_running_loop()
        optional_length_retry_seconds = (
            _DE_AI_LOCAL_CLI_OPTIONAL_LENGTH_RETRY_SECONDS
            if provider_is_local_cli
            else _DE_AI_API_OPTIONAL_LENGTH_RETRY_SECONDS
        )
        generation_timeout_seconds = (
            _DE_AI_LOCAL_CLI_GENERATION_TIMEOUT_SECONDS
            if provider_is_local_cli
            else _DE_AI_API_GENERATION_TIMEOUT_SECONDS
        )

        async def generate_chunk(
            chunk_index: int,
            chunk_prompt: str,
            *,
            max_attempts: int = 3,
            retry_usable_length: bool = True,
        ) -> tuple[str, dict[str, Any]]:
            chunk_text = ""
            chunk_result: dict[str, Any] = {}
            best_chunk_text = ""
            best_chunk_result: dict[str, Any] = {}
            best_length_rank: tuple[int, int, int] | None = None
            active_prompt = chunk_prompt
            target = parse_de_ai_chunk_target(chunk_prompt)
            optional_length_retry_deadline: float | None = None
            for attempt in range(max_attempts):
                # The first complete scene is mandatory.  Further calls only
                # fine-tune its length, so never let those optional retries
                # keep a usable whole-chapter candidate hidden indefinitely.
                if (
                    attempt > 0
                    and optional_length_retry_deadline is not None
                    and event_loop.time() >= optional_length_retry_deadline
                ):
                    break
                async with semaphore:
                    chunk_result = await LLMGateway.chat_completion(
                        messages=[
                            rewrite_system_message,
                            {"role": "user", "content": active_prompt},
                        ],
                        model=model,
                        temperature=0.95,
                        max_tokens=2_500,
                        timeout=generation_timeout_seconds,
                        retry=1,
                        extra_body=extra_body,
                    )
                chunk_text = _clean_revision_output(chunk_result.get("content"))
                if attempt == 0:
                    # A slow mandatory first generation must not consume the
                    # optional retry budget before a candidate even exists.
                    # Start the bounded window only after that first response.
                    optional_length_retry_deadline = (
                        event_loop.time() + optional_length_retry_seconds
                    )
                visible_length = count_de_ai_visible_characters(chunk_text)
                if len(chunk_text) >= 40:
                    length_rank = de_ai_chunk_length_rank(visible_length, target)
                    if best_length_rank is None or length_rank < best_length_rank:
                        best_chunk_text = chunk_text
                        best_chunk_result = chunk_result
                        best_length_rank = length_rank
                length_issue = ""
                if (
                    target
                    and target[0] >= 100
                    and visible_length < target[0]
                ):
                    length_issue = (
                        f"上一稿只有约{visible_length}个可见字符，明显短于本段目标"
                        f"{target[0]}至{target[1]}字；保留同一批事实，不复述结论，"
                        "用账本已有动作和对白的现场进程补足篇幅。"
                    )
                elif (
                    target
                    and target[0] >= 100
                    and visible_length > target[1]
                ):
                    length_issue = (
                        f"上一稿约{visible_length}个可见字符，超过本段目标"
                        f"{target[0]}至{target[1]}字；删掉解释、同义复述和可选陈设，"
                        "不得删除硬事实。"
                    )
                if (
                    len(chunk_text) >= 40
                    and (not length_issue or not retry_usable_length)
                ):
                    break
                active_prompt = build_de_ai_chunk_repair_prompt(
                    chunk_prompt,
                    [{"kind": "length", "detail": length_issue}],
                    previous_candidate=chunk_text,
                )
            if not best_chunk_text:
                raise LLMError(
                    "去除 AI 味失败："
                    f"模型没有生成可用的场景片段（{chunk_index}/{len(chunk_prompts)}）"
                )
            return best_chunk_text, best_chunk_result

        chunk_outputs = await asyncio.gather(*(
            generate_chunk(chunk_index, chunk_prompt)
            for chunk_index, chunk_prompt in enumerate(chunk_prompts, start=1)
        ))
        chunk_texts = [item[0] for item in chunk_outputs]
        result = chunk_outputs[-1][1]
        review_deadline = event_loop.time() + (
            _DE_AI_LOCAL_CLI_REVIEW_TIMEOUT_SECONDS
            if provider_is_local_cli
            else _DE_AI_API_REVIEW_TIMEOUT_SECONDS
        )

        def review_remaining_seconds() -> float:
            return review_deadline - event_loop.time()

        async def review_chat_completion(**kwargs: Any) -> dict[str, Any]:
            """Bound optional review work after a complete candidate exists."""

            remaining = review_remaining_seconds()
            if remaining <= 0.05:
                raise TimeoutError(
                    "候选稿已生成；系统审核达到本轮时限，请对照原文确认"
                )
            requested_timeout = float(kwargs.get("timeout") or remaining)
            kwargs["timeout"] = max(1, min(requested_timeout, remaining))
            try:
                return await asyncio.wait_for(
                    LLMGateway.chat_completion(**kwargs),
                    timeout=remaining,
                )
            except TimeoutError as exc:
                raise TimeoutError(
                    "候选稿已生成；系统审核达到本轮时限，请对照原文确认"
                ) from exc

        fidelity_issue_history: dict[int, list[dict[str, Any]]] = {}

        def remember_fidelity_issue(issue: dict[str, Any]) -> None:
            try:
                chunk_index = int(issue.get("chunk") or 0)
            except (TypeError, ValueError):
                return
            if chunk_index < 1 or chunk_index > len(chunk_texts):
                return
            detail = str(issue.get("detail") or "").strip()
            if not detail:
                return
            history = fidelity_issue_history.setdefault(chunk_index, [])
            identity = (str(issue.get("kind") or ""), detail)
            if identity not in {
                (str(item.get("kind") or ""), str(item.get("detail") or ""))
                for item in history
            }:
                history.append(dict(issue))

        def remember_fidelity_audit(audit: dict[str, Any]) -> None:
            for issue in audit.get("issues", []):
                if isinstance(issue, dict):
                    remember_fidelity_issue(issue)

        async def audit_chunks(values: list[str]) -> dict[str, Any]:
            try:
                audit_prompt = build_de_ai_fidelity_audit_prompt(original, values)
                focus_lines = "\n".join(
                    f"- 第{chunk_index}段：{str(issue.get('detail') or '').strip()}"
                    for chunk_index in sorted(fidelity_issue_history)
                    for issue in fidelity_issue_history[chunk_index]
                    if str(issue.get("detail") or "").strip()
                )
                if focus_lines:
                    audit_prompt += (
                        "\n\n【历史问题仅作复核线索】\n"
                        "下列问题曾在较早候选中出现。必须重新以本轮不可变原文和当前候选为准；"
                        "只有当前候选仍存在同一事实错误时才报告。若历史意见与原文、当前候选或"
                        "另一条历史意见冲突，不得沿用历史结论。原文自身存在未交代的状态跳变时，"
                        "只核对候选是否保留两端状态，不要求补写中间过程。\n"
                        + focus_lines
                    )
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是小说事实校对员。只核对候选正文是否完整、准确保留原文故事，"
                            "不评价文风，不重写正文。严格按用户要求只输出 JSON。"
                        ),
                    },
                    {"role": "user", "content": audit_prompt},
                ]
                audit: dict[str, Any] = {
                    "valid": False,
                    "passed": False,
                    "issues": [],
                }
                for _protocol_attempt in range(2):
                    audit_result = await review_chat_completion(
                        messages=messages,
                        model=model,
                        temperature=0,
                        max_tokens=2_500,
                        timeout=600,
                        retry=1,
                        extra_body=extra_body,
                    )
                    audit = parse_de_ai_fidelity_audit(
                        audit_result.get("content"),
                        chunk_count=len(values),
                    )
                    if audit["valid"]:
                        remember_fidelity_audit(audit)
                        return audit
                    messages = [
                        *messages,
                        {
                            "role": "assistant",
                            "content": _clean_revision_output(
                                audit_result.get("content")
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "上一条没有满足输出协议。不要重新分析或解释；请把同一审计"
                                "结论改成唯一一个合法 JSON 对象，且只含布尔 passed 与数组 issues。"
                            ),
                        },
                    ]
                remember_fidelity_audit(audit)
                return audit
            except Exception as exc:
                return _audit_runtime_failure("故事保真审计", exc)

        async def audit_style_chunks(values: list[str]) -> dict[str, Any]:
            try:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是中文小说表达结构审计员。只识别成品化机器叙事结构，"
                            "不核对故事事实，不重写正文。严格按用户要求只输出 JSON。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_de_ai_style_audit_prompt(values),
                    },
                ]
                audit: dict[str, Any] = {
                    "valid": False,
                    "passed": False,
                    "issues": [],
                }
                for _protocol_attempt in range(2):
                    audit_result = await review_chat_completion(
                        messages=messages,
                        model=model,
                        temperature=0,
                        max_tokens=2_500,
                        timeout=600,
                        retry=1,
                        extra_body=extra_body,
                    )
                    audit = parse_de_ai_style_audit(
                        audit_result.get("content"),
                        chunk_count=len(values),
                    )
                    if audit["valid"]:
                        return audit
                    messages = [
                        *messages,
                        {
                            "role": "assistant",
                            "content": _clean_revision_output(
                                audit_result.get("content")
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "上一条没有满足输出协议。不要重新分析或解释；请把同一审计"
                                "结论改成唯一一个合法 JSON 对象，且只含布尔 passed 与数组 issues。"
                            ),
                        },
                    ]
                return audit
            except Exception as exc:
                return _audit_runtime_failure("表达结构审计", exc)

        def collect_repair_issues(
            audit: dict[str, Any],
        ) -> dict[int, list[dict[str, Any]]]:
            collected: dict[int, list[dict[str, Any]]] = {}
            assessment = assess_de_ai_revision(
                original,
                "\n\n".join(chunk_texts),
                require_substantial_revision=False,
            )
            for token in assessment.get("missing_protected_tokens", []):
                matching_chunk = next(
                    (
                        index
                        for index, prompt in enumerate(detailed_chunk_prompts, start=1)
                        if str(token) in prompt
                    ),
                    None,
                )
                if matching_chunk is not None:
                    collected.setdefault(matching_chunk, []).append({
                        "chunk": matching_chunk,
                        "kind": "missing",
                        "detail": f"上一稿遗漏了必须原字保留的故事标记：{token}",
                    })
            for issue in audit.get("issues", []):
                chunk_index = int(issue["chunk"])
                collected.setdefault(chunk_index, []).append(issue)
            return collected

        issue_history: dict[int, list[dict[str, Any]]] = {}
        async def repair_fidelity(
            audit: dict[str, Any],
        ) -> dict[str, Any]:
            nonlocal result
            if not audit["valid"]:
                return _mark_audit_exhausted(audit)
            pending_issues: dict[int, list[dict[str, Any]]] = {}
            for repair_attempt in range(1, 6):
                pending_issues = collect_repair_issues(audit)
                if not pending_issues:
                    break
                current_issue_history: dict[int, list[dict[str, Any]]] = {}
                for chunk_index, issues in pending_issues.items():
                    for issue in issues:
                        remember_fidelity_issue(issue)
                    # Repair only what the current audit still confirms.  Old
                    # findings remain hints for the next independent audit,
                    # not permanent prose constraints; otherwise conflicting
                    # audits can make a faithful source impossible to satisfy.
                    current_issue_history[chunk_index] = [dict(issue) for issue in issues]
                    issue_history[chunk_index] = current_issue_history[chunk_index]
                def fidelity_repair_prompt(
                    chunk_index: int,
                    *,
                    active_repair_attempt: int = repair_attempt,
                    active_issue_history: dict[
                        int,
                        list[dict[str, Any]],
                    ] = current_issue_history,
                ) -> str:
                    history = active_issue_history[chunk_index]
                    if any(
                        str(item.get("kind") or "").startswith("style:")
                        for item in history
                    ):
                        return build_de_ai_style_repair_prompt(
                            chunk_prompts[chunk_index - 1],
                            history,
                            repair_attempt=active_repair_attempt,
                            previous_candidate=chunk_texts[chunk_index - 1],
                            fidelity_chunk_prompt=(
                                detailed_chunk_prompts[chunk_index - 1]
                            ),
                        )
                    return build_de_ai_chunk_repair_prompt(
                        detailed_chunk_prompts[chunk_index - 1],
                        history,
                        repair_attempt=active_repair_attempt,
                        previous_candidate=chunk_texts[chunk_index - 1],
                    )

                try:
                    remaining = review_remaining_seconds()
                    if remaining <= 0.05:
                        raise TimeoutError(
                            "候选稿已生成；故事保真修复达到本轮时限"
                        )
                    repair_outputs = await asyncio.wait_for(
                        asyncio.gather(*(
                            generate_chunk(
                                chunk_index,
                                fidelity_repair_prompt(chunk_index),
                                # A factual correction receives the previous
                                # usable prose and should change only the
                                # audited facts.  Do not spend the entire
                                # review window generating three length
                                # variants before the corrected chapter can be
                                # checked.  A second call remains available
                                # only when the first response is empty.
                                max_attempts=2,
                                retry_usable_length=False,
                            )
                            for chunk_index in sorted(pending_issues)
                        )),
                        timeout=remaining,
                    )
                except Exception as exc:
                    return _mark_audit_exhausted(
                        audit,
                        detail=(
                            "故事保真修复未能完成："
                            f"{str(exc).strip() or exc.__class__.__name__}"
                        ),
                        repair_attempts=repair_attempt - 1,
                    )
                proposed_chunk_texts = list(chunk_texts)
                for chunk_index, repair_output in zip(
                    sorted(pending_issues),
                    repair_outputs,
                    strict=True,
                ):
                    proposed_chunk_texts[chunk_index - 1] = repair_output[0]
                proposed_assessment = assess_de_ai_revision(
                    original,
                    "\n\n".join(proposed_chunk_texts),
                    require_substantial_revision=False,
                )
                if not proposed_assessment["accepted"]:
                    # The immutable original and chapter floor outrank a
                    # purported fact fix.  Try the same focused correction in
                    # the next bounded repair pass without replacing the last
                    # usable whole-chapter candidate.
                    continue
                chunk_texts[:] = proposed_chunk_texts
                result = repair_outputs[-1][1]
                audit = await audit_chunks(chunk_texts)
                if not audit["valid"]:
                    return _mark_audit_exhausted(
                        audit,
                        repair_attempts=repair_attempt,
                    )

            pending_issues = collect_repair_issues(audit)
            if pending_issues or not audit["passed"]:
                return _mark_audit_exhausted(
                    audit,
                    repair_attempts=5,
                )
            return audit

        fidelity_audit = await repair_fidelity(await audit_chunks(chunk_texts))

        style_audit = await audit_style_chunks(chunk_texts)
        style_issue_history: dict[int, list[dict[str, Any]]] = {}

        def style_candidate_score(
            values: list[str],
            fidelity: dict[str, Any],
            audit: dict[str, Any],
        ) -> tuple[int, int, int, int, int, int, int, int, int]:
            assessment = assess_de_ai_revision(
                original,
                "\n\n".join(values),
                require_substantial_revision=False,
            )
            issues = audit.get("issues", [])
            history = [
                item
                for values in style_issue_history.values()
                for item in values
            ]
            issue_count, issue_severity, novel_kinds, novel_pairs = (
                de_ai_style_issue_rank(issues, history)
            )
            return (
                0 if assessment["accepted"] else 1,
                0 if fidelity.get("valid") and fidelity.get("passed") else 1,
                0 if audit.get("valid") else 1,
                0 if audit.get("passed") and not issues else 1,
                issue_count,
                issue_severity,
                novel_kinds,
                novel_pairs,
                # With revision/fidelity floors already satisfied, prefer the
                # tighter branch when structural issue counts tie. The longer
                # branch usually retains more of the staged/checklist padding
                # this pass exists to remove.
                count_de_ai_visible_characters("\n\n".join(values)),
            )

        best_chunk_texts = list(chunk_texts)
        best_fidelity_audit = fidelity_audit
        best_style_audit = style_audit
        best_style_score = style_candidate_score(
            chunk_texts,
            fidelity_audit,
            style_audit,
        )
        style_repair_attempts = 0
        for style_attempt in range(1, DE_AI_STRUCTURAL_REPAIR_ATTEMPTS + 1):
            if not style_audit.get("valid"):
                break
            pending_style_issues: dict[int, list[dict[str, Any]]] = {}
            for issue in style_audit.get("issues", []):
                chunk_index = int(issue["chunk"])
                pending_style_issues.setdefault(chunk_index, []).append(issue)
            if not pending_style_issues:
                break
            style_repair_attempts = style_attempt
            chapter_floor = (
                max(2_000, round(count_de_ai_visible_characters(original) * 0.95))
                if count_de_ai_visible_characters(original) >= 2_000
                else round(count_de_ai_visible_characters(original) * 0.9)
            )
            preserved_visible = sum(
                count_de_ai_visible_characters(value)
                for index, value in enumerate(chunk_texts, start=1)
                if index not in pending_style_issues
            )
            repair_visible = sum(
                count_de_ai_visible_characters(chunk_texts[index - 1])
                for index in pending_style_issues
            )
            remaining_floor = max(0, chapter_floor - preserved_visible)
            for chunk_index, issues in pending_style_issues.items():
                history = style_issue_history.setdefault(chunk_index, [])
                known = {
                    (str(item.get("kind") or ""), str(item.get("detail") or ""))
                    for item in history
                }
                for issue in issues:
                    identity = (
                        str(issue.get("kind") or ""),
                        str(issue.get("detail") or ""),
                    )
                    if identity not in known:
                        history.append(issue)
                        known.add(identity)
            # Compare every branch against the same accumulated issue
            # history.  The baseline was first scored before that history was
            # populated; leaving its old novelty score in place would make an
            # otherwise unchanged repair look better merely because its
            # defects have now been seen once.
            best_style_score = style_candidate_score(
                best_chunk_texts,
                best_fidelity_audit,
                best_style_audit,
            )
            try:
                remaining = review_remaining_seconds()
                if remaining <= 0.05:
                    raise TimeoutError(
                        "候选稿已生成；表达结构修复达到本轮时限"
                    )
                repair_outputs = await asyncio.wait_for(
                    asyncio.gather(*(
                        generate_chunk(
                            chunk_index,
                            build_de_ai_style_repair_prompt(
                                chunk_prompts[chunk_index - 1],
                                style_issue_history[chunk_index],
                                repair_attempt=style_attempt,
                                allow_target_shrink=False,
                                minimum_target_characters=(
                                    round(
                                        remaining_floor
                                        * count_de_ai_visible_characters(
                                            chunk_texts[chunk_index - 1]
                                        )
                                        / max(1, repair_visible)
                                    )
                                ),
                                previous_candidate=chunk_texts[chunk_index - 1],
                                fidelity_chunk_prompt=(
                                    detailed_chunk_prompts[chunk_index - 1]
                                ),
                            ),
                            max_attempts=DE_AI_STRUCTURAL_OUTPUT_ATTEMPTS,
                        )
                        for chunk_index in sorted(pending_style_issues)
                    )),
                    timeout=remaining,
                )
            except Exception as exc:
                style_audit = _mark_audit_exhausted(
                    style_audit,
                    detail=f"表达结构修复未能完成：{str(exc).strip() or exc.__class__.__name__}",
                    repair_attempts=style_attempt - 1,
                )
                break
            for chunk_index, repair_output in zip(
                sorted(pending_style_issues),
                repair_outputs,
                strict=True,
            ):
                chunk_texts[chunk_index - 1] = repair_output[0]
            result = repair_outputs[-1][1]
            branch_fidelity_audit = await audit_chunks(chunk_texts)
            branch_assessment = assess_de_ai_revision(
                original,
                "\n\n".join(chunk_texts),
                require_substantial_revision=False,
            )
            if not is_de_ai_structural_branch_repairable(
                branch_fidelity_audit,
                missing_protected_tokens=branch_assessment.get(
                    "missing_protected_tokens",
                    [],
                ),
            ):
                chunk_texts = list(best_chunk_texts)
                fidelity_audit = best_fidelity_audit
                style_audit = best_style_audit
                continue
            fidelity_audit = await repair_fidelity(branch_fidelity_audit)
            style_audit = await audit_style_chunks(chunk_texts)
            if not style_audit["valid"]:
                break
            candidate_score = style_candidate_score(
                chunk_texts,
                fidelity_audit,
                style_audit,
            )
            if candidate_score < best_style_score:
                best_chunk_texts = list(chunk_texts)
                best_fidelity_audit = fidelity_audit
                best_style_audit = style_audit
                best_style_score = candidate_score

        chunk_texts = best_chunk_texts
        fidelity_audit = best_fidelity_audit
        style_audit = best_style_audit
        if style_audit.get("issues") or not style_audit.get("passed"):
            style_audit = {
                **style_audit,
                "exhausted": True,
                "selected_best": True,
                "repair_attempts": style_repair_attempts,
            }
        rewritten = "\n\n".join(chunk_texts)
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"去除 AI 味失败：{exc}") from exc

    return _build_revision_preview(
        chapter=chapter,
        original=original,
        source=source,
        rewritten=rewritten,
        result=result,
        model=model,
        round_number=round_number,
        fidelity_audit=fidelity_audit,
        style_audit=style_audit,
    )
