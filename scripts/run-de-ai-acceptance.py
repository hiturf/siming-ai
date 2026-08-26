"""Run reproducible long-chapter de-AI acceptance calls through Siming.

This harness intentionally uses the public ``/chat/completion`` route.  It
therefore exercises the same provider gateway as the chapter preview endpoint
without creating projects or chapters in the user's library.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.ai.local_cli_adapter import (  # noqa: E402
    DEFAULT_CLI_ARGS,
    DEFAULT_CLI_COMMANDS,
    LocalCLIAdapter,
)
from app.prompts.anti_ai_prompts import (  # noqa: E402
    apply_de_ai_macro_ledger,
    build_de_ai_candidate_preserving_expansion_prompt,
    build_de_ai_chunk_repair_prompt,
    build_de_ai_chunked_rewrite_prompts,
    build_de_ai_detector_feedback_repair_prompt,
    build_de_ai_detector_ledger_compression_prompt,
    build_de_ai_fidelity_audit_prompt,
    build_de_ai_macro_ledger_compression_prompt,
    build_de_ai_macro_ledger_retry_feedback,
    build_de_ai_rewrite_from_ledger_prompt,
    build_de_ai_story_ledger_prompt,
    build_de_ai_style_audit_prompt,
    build_de_ai_style_repair_prompt,
    normalize_de_ai_macro_ledger,
    validate_de_ai_macro_ledger,
)
from app.prompts.packs.chapter_quality import PACK as CHAPTER_PACK  # noqa: E402
from app.prompts.style_prompts import build_style_context  # noqa: E402
from app.services.de_ai_validation import (  # noqa: E402
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

SCENARIOS = {
    "warehouse": {
        "title": "A17仓库的夜班交接",
        "outline": (
            "都市悬疑，第三人称限知，视角锁定周砚。7月12日晚，周砚带着3封没有署名的信，"
            "必须在21点前送到A17仓库交给陈禾。陈禾发现其中一封封口换过，但不肯解释。"
            "停电后，两人听见北门卷帘被人抬起；周砚用旧收音机的反光确认门外有两个人。"
            "陈禾承认第三封信是假的，真正的账页藏在收音机电池仓。两人没有报警，借叉车"
            "制造声响，从南侧装卸口离开。章末停在周砚发现车后座多了一枚A17储物柜钥匙。"
        ),
    },
    "ferry": {
        "title": "停航前的最后一班渡船",
        "outline": (
            "现实题材，第一人称，叙述者是修表匠许见川。腊月二十六下午4点10分，他带着"
            "编号K-09的旧怀表赶最后一班渡船去白石镇。船工罗叔认出怀表属于十二年前失踪的"
            "教师沈遥，却故意说不认识。江面起雾，发动机熄火；许见川从表壳夹层找到一张只写"
            "了‘六码头、17号柜’的票根。罗叔最后承认当年替沈遥保管过一只木箱，但木箱三天前"
            "被人取走。船靠岸后，许见川没有追问，发现自己的工具包里多了一截湿麻绳。"
        ),
    },
}

_CHECKPOINT_VERSION = 2
_DETECTOR_FIDELITY_REPAIR_ATTEMPTS = 3


def _content_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _preserves_non_whitespace_characters(source: str, candidate: str) -> bool:
    """Return whether candidate keeps every source non-whitespace char in order."""

    expected = "".join(str(source or "").split())
    actual = "".join(str(candidate or "").split())
    if not expected:
        return True
    position = 0
    for character in actual:
        if character == expected[position]:
            position += 1
            if position == len(expected):
                return True
    return False


def _new_checkpoint(
    source: str,
    model: str,
    *,
    authority_source: str | None = None,
) -> dict:
    authority = str(source if authority_source is None else authority_source)
    return {
        "version": _CHECKPOINT_VERSION,
        "source_sha256": _content_hash(source),
        "authority_sha256": _content_hash(authority),
        "model": model,
        "story_ledger": "",
        "outputs": {},
        "audits": {},
        "style_audits": {},
    }


def _load_checkpoint(
    path: Path | None,
    *,
    source: str,
    model: str,
    authority_source: str | None = None,
) -> dict:
    expected = _new_checkpoint(
        source,
        model,
        authority_source=authority_source,
    )
    if path is None or not path.exists():
        return expected
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return expected
    if not isinstance(payload, dict):
        return expected
    version = payload.get("version")
    legacy_same_authority = (
        version == 1
        and expected["source_sha256"] == expected["authority_sha256"]
    )
    if (
        version not in {1, _CHECKPOINT_VERSION}
        or (version == 1 and not legacy_same_authority)
        or payload.get("source_sha256") != expected["source_sha256"]
        or (
            version == _CHECKPOINT_VERSION
            and payload.get("authority_sha256") != expected["authority_sha256"]
        )
        or payload.get("model") != model
    ):
        return expected
    if version == 1:
        # Version-one checkpoints predate multi-round lineage.  They are safe
        # only when the rewritten input and factual authority are identical.
        payload["version"] = _CHECKPOINT_VERSION
        payload["authority_sha256"] = expected["authority_sha256"]
    payload.setdefault("story_ledger", "")
    payload.setdefault("outputs", {})
    payload.setdefault("audits", {})
    payload.setdefault("style_audits", {})
    return payload


def _restore_fidelity_issue_history(
    cached_audits: object,
    *,
    chunk_count: int,
) -> dict[int, list[dict]]:
    """Recover every concrete fact issue stored for one checkpoint identity."""

    history: dict[int, list[dict]] = {}
    if not isinstance(cached_audits, dict):
        return history
    for cached_audit in cached_audits.values():
        if not isinstance(cached_audit, dict):
            continue
        try:
            audit = parse_de_ai_fidelity_audit(
                str(cached_audit.get("content") or ""),
                chunk_count=chunk_count,
            )
        except (TypeError, ValueError):
            continue
        for issue in audit.get("issues", []):
            index = int(issue.get("chunk") or 0) - 1
            if index < 0 or index >= chunk_count:
                continue
            items = history.setdefault(index, [])
            identity = (
                str(issue.get("kind") or ""),
                str(issue.get("detail") or ""),
            )
            if identity not in {
                (str(item.get("kind") or ""), str(item.get("detail") or ""))
                for item in items
            }:
                items.append(dict(issue))
    return history


def _write_checkpoint(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    encoded_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    body = ""
    for attempt in range(1, 4):
        request = Request(
            url,
            data=encoded_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise RuntimeError(
                    f"Siming returned HTTP {exc.code}: {detail}"
                ) from exc
        except (URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            if attempt == 3:
                raise RuntimeError(f"Cannot reach Siming: {exc}") from exc
        print(
            f"de-ai: transient gateway failure; retrying request {attempt + 1}/3",
            flush=True,
        )
        time.sleep(attempt)
    parsed = json.loads(body)
    if int(parsed.get("code", -1)) != 0:
        raise RuntimeError(str(parsed.get("message") or parsed))
    return parsed["data"]


def _get_json(url: str, timeout: int = 30) -> dict:
    try:
        with urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Cannot read Siming config metadata: {exc}") from exc
    parsed = json.loads(body)
    if int(parsed.get("code", -1)) != 0:
        raise RuntimeError(str(parsed.get("message") or parsed))
    return parsed["data"]


def _chat(base_url: str, *, messages: list[dict], model: str, temperature: float) -> dict:
    return _post_json(
        f"{base_url.rstrip('/')}/api/v1/chat/completion",
        {
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": 12_000,
            "extra_body": {
                "moshu_task_type": "writing",
                "local_cli_isolated": True,
                "local_cli_allow_mcp": False,
                "local_cli_timeout_seconds": 600,
            },
        },
        timeout=720,
    )


def _direct_local_cli_chat(
    base_url: str,
    *,
    messages: list[dict],
    model: str,
    timeout_seconds: int = 900,
) -> dict:
    provider, model_name = model.split(":", 1)
    try:
        config = _get_json(f"{base_url.rstrip('/')}/api/v1/config/models/{provider}")
    except RuntimeError:
        # The checked-out adapter has complete safe defaults for built-in CLI
        # providers.  Falling back keeps the acceptance harness usable when the
        # development API is intentionally stopped or restarted.
        config = {
            "cli_command": DEFAULT_CLI_COMMANDS.get(provider) or "",
            "cli_args": json.dumps(
                DEFAULT_CLI_ARGS.get(provider, ["{prompt}"]),
                ensure_ascii=False,
            ),
        }
    adapter = LocalCLIAdapter(
        api_key="",
        base_url=provider,
        cli_command=str(config.get("cli_command") or ""),
        cli_args=str(config.get("cli_args") or "") or None,
    )
    result = asyncio.run(adapter.chat_completion(
        messages=messages,
        model=model_name,
        temperature=0.5,
        max_tokens=12_000,
        extra_body={
            "moshu_task_type": "writing",
            "local_cli_isolated": True,
            "local_cli_allow_mcp": False,
            "local_cli_timeout_seconds": timeout_seconds,
        },
    ))
    result["request_meta"] = {"provider": provider, "model": model_name}
    return result


def _style_context() -> str:
    project = SimpleNamespace(
        narrative_perspective="third_person",
        writing_style="natural",
        short_sentences=False,
        custom_style_prompt="",
        forbidden_sentence_patterns="",
        rhetoric_guidelines="",
    )
    return build_style_context(project, include_anti_ai=False, concise=True)


def _generate_source(base_url: str, model: str, scenario: dict) -> dict:
    system = CHAPTER_PACK.build_system_prompt(
        style_context="叙事视角服从本轮大纲。文风自然、克制，人物说话符合身份。",
    )
    user = (
        f"请根据下列细纲写一章完整正文，正文目标2200至2600个中文字符。不要写章节标题，"
        f"不要解释创作过程，不得省略细纲中的数字、编号、物件和事件。\n\n"
        f"【章节名（仅供理解，不要输出）】{scenario['title']}\n"
        f"【细纲】{scenario['outline']}"
    )
    return _chat(
        base_url,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0.8,
    )


def _revise(
    base_url: str,
    model: str,
    source: str,
    *,
    authority_source: str | None = None,
    direct_local_cli: bool,
    story_ledger_override: str = "",
    checkpoint_path: Path | None = None,
    local_cli_timeout_seconds: int = 900,
    holistic_revision: bool = False,
) -> dict:
    authority = str(source if authority_source is None else authority_source)
    checkpoint = _load_checkpoint(
        checkpoint_path,
        source=source,
        model=model,
        authority_source=authority,
    )
    checkpoint_lock = Lock()

    def checkpoint_get(section: str, key: str) -> dict | None:
        with checkpoint_lock:
            values = checkpoint.get(section)
            if not isinstance(values, dict):
                return None
            item = values.get(key)
            return dict(item) if isinstance(item, dict) else None

    def checkpoint_put(section: str, key: str, value: dict) -> None:
        with checkpoint_lock:
            values = checkpoint.setdefault(section, {})
            values[key] = value
            _write_checkpoint(checkpoint_path, checkpoint)

    story_ledger = str(story_ledger_override or "").strip()
    if story_ledger:
        print("de-ai: reusing acceptance story ledger", flush=True)
    elif str(checkpoint.get("story_ledger") or "").strip():
        story_ledger = str(checkpoint["story_ledger"]).strip()
        print("de-ai: restoring story ledger from checkpoint", flush=True)
    else:
        print("de-ai: extracting story ledger", flush=True)
        ledger_messages = [
            {
                "role": "system",
                "content": (
                    "你是小说事实记录员。只抽取原文已经存在的事实、事件顺序、线索和对白意图，"
                    "不得续写、解释、评价或添加细节。输出紧凑的事件账本，不输出小说正文。"
                ),
            },
            {"role": "user", "content": build_de_ai_story_ledger_prompt(authority)},
        ]
        if direct_local_cli:
            ledger_result = _direct_local_cli_chat(
                base_url,
                messages=ledger_messages,
                model=model,
                timeout_seconds=local_cli_timeout_seconds,
            )
        else:
            ledger_result = _chat(
                base_url,
                messages=ledger_messages,
                model=model,
                temperature=0.1,
            )
        story_ledger = str(ledger_result.get("content") or "").strip()
    if story_ledger.startswith("```"):
        ledger_lines = story_ledger.splitlines()[1:]
        if ledger_lines and ledger_lines[-1].strip() == "```":
            ledger_lines = ledger_lines[:-1]
        story_ledger = "\n".join(ledger_lines).strip()
    if len(story_ledger) < 80:
        raise RuntimeError("Revision model returned an unusable story ledger")
    with checkpoint_lock:
        checkpoint["story_ledger"] = story_ledger
        _write_checkpoint(checkpoint_path, checkpoint)

    chunk_prompts = (
        [
            build_de_ai_rewrite_from_ledger_prompt(
                source,
                story_ledger,
                fidelity_source=authority,
            )
        ]
        if holistic_revision
        else build_de_ai_chunked_rewrite_prompts(
            source,
            story_ledger,
            fidelity_source=authority,
        )
    )
    if not chunk_prompts:
        raise RuntimeError("Revision model returned an unusable story ledger split")
    detailed_chunk_prompts = list(chunk_prompts)
    scope = "完整章节" if holistic_revision else "整章事实账本中的一个连续片段"
    system = (
        f"你是中文小说的重写编辑。给定内容是{scope}。"
        "只能重写表达，不规划剧情，不新增或删改事实、人物、设定、事件、线索或关系。"
        "直接输出小说正文，不要标题、说明、清单、Markdown 或修改报告。\n\n"
        f"【作品文风】\n{_style_context()}"
    )
    if model.startswith("opencode_cli:"):
        worker_limit = 1
    elif model.startswith("codex_cli:"):
        worker_limit = 2
    else:
        worker_limit = 3

    if count_de_ai_visible_characters(authority) >= 1_500 and not holistic_revision:
        def compact_chunk_prompt(item: tuple[int, str]) -> str:
            chunk_index, chunk_prompt = item
            compact_prompt = build_de_ai_macro_ledger_compression_prompt(chunk_prompt)
            compact_key = _content_hash(f"macro-ledger-v8-bounded-retry\n{compact_prompt}")
            cached = checkpoint_get("macro_ledgers", compact_key)
            cached_ledger = str((cached or {}).get("content") or "").strip()
            cached_valid, _ = validate_de_ai_macro_ledger(
                chunk_prompt,
                cached_ledger,
            )
            validation_mode = str((cached or {}).get("validation_mode") or "")
            if (
                validation_mode == "deterministic-final-source-audit-v1"
                and (cached or {}).get("fallback_to_detailed")
            ):
                print(
                    f"de-ai: restoring detailed-ledger fallback "
                    f"{chunk_index}/{len(chunk_prompts)}",
                    flush=True,
                )
                return chunk_prompt
            if (
                len(cached_ledger) >= 40
                and cached_valid
                and validation_mode == "deterministic-final-source-audit-v1"
            ):
                print(
                    f"de-ai: restoring macro ledger {chunk_index}/{len(chunk_prompts)}",
                    flush=True,
                )
                compact_ledger = cached_ledger
            else:
                print(
                    f"de-ai: compressing macro ledger {chunk_index}/{len(chunk_prompts)}",
                    flush=True,
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
                compact_ledger = ""
                compact_result: dict = {}
                valid_compact = False
                missing: list[str] = []
                for compact_attempt in range(2):
                    if direct_local_cli:
                        compact_result = _direct_local_cli_chat(
                            base_url,
                            messages=messages,
                            model=model,
                            timeout_seconds=local_cli_timeout_seconds,
                        )
                    else:
                        compact_result = _chat(
                            base_url,
                            messages=messages,
                            model=model,
                            temperature=0,
                        )
                    compact_ledger = str(compact_result.get("content") or "").strip()
                    if compact_ledger.startswith("```"):
                        compact_ledger = _clean_generated_text(compact_ledger)
                    compact_ledger = normalize_de_ai_macro_ledger(compact_ledger)
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
                if not valid_compact:
                    checkpoint_put("macro_ledgers", compact_key, {
                        "content": compact_ledger,
                        "fallback_to_detailed": True,
                        "validation_mode": "deterministic-final-source-audit-v1",
                        "model": compact_result.get("model") or "",
                        "request_meta": compact_result.get("request_meta") or {},
                    })
                    return chunk_prompt
                checkpoint_put("macro_ledgers", compact_key, {
                    "content": compact_ledger,
                    "validation_mode": "deterministic-final-source-audit-v1",
                    "model": compact_result.get("model") or "",
                    "request_meta": compact_result.get("request_meta") or {},
                })
            # Final assembled-candidate auditing against the immutable source
            # remains the story-safety authority for both API and CLI paths.
            return apply_de_ai_macro_ledger(chunk_prompt, compact_ledger)

        indexed_compaction_prompts = list(enumerate(chunk_prompts, start=1))
        with ThreadPoolExecutor(
            max_workers=min(worker_limit, len(indexed_compaction_prompts)),
        ) as executor:
            chunk_prompts = list(executor.map(
                compact_chunk_prompt,
                indexed_compaction_prompts,
            ))

    def generate_chunk(
        item: tuple[int, str] | tuple[int, str, int],
    ) -> tuple[str, dict]:
        chunk_index, chunk_prompt = item[:2]
        max_attempts = int(item[2]) if len(item) > 2 else 3
        checkpoint_key = _content_hash(f"{system}\n\n{chunk_prompt}")
        cached = checkpoint_get("outputs", checkpoint_key)
        if cached and len(str(cached.get("content") or "").strip()) >= 40:
            print(
                f"de-ai: restoring scene {chunk_index}/{len(chunk_prompts)} from checkpoint",
                flush=True,
            )
            return str(cached["content"]).strip(), cached
        chunk_text = ""
        chunk_result: dict = {}
        best_chunk_text = ""
        best_chunk_result: dict = {}
        best_length_rank: tuple[int, int, int] | None = None
        active_prompt = chunk_prompt
        target = parse_de_ai_chunk_target(chunk_prompt)
        for attempt in range(1, max_attempts + 1):
            print(
                f"de-ai: rewriting scene {chunk_index}/{len(chunk_prompts)}"
                + (f" (retry {attempt})" if attempt > 1 else ""),
                flush=True,
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": active_prompt},
            ]
            if direct_local_cli:
                chunk_result = _direct_local_cli_chat(
                    base_url,
                    messages=messages,
                    model=model,
                    timeout_seconds=local_cli_timeout_seconds,
                )
            else:
                chunk_result = _chat(base_url, messages=messages, model=model, temperature=0.95)
            chunk_text = str(chunk_result.get("content") or "").strip()
            visible_length = count_de_ai_visible_characters(chunk_text)
            if len(chunk_text) >= 40:
                length_rank = de_ai_chunk_length_rank(visible_length, target)
                if best_length_rank is None or length_rank < best_length_rank:
                    best_chunk_text = chunk_text
                    best_chunk_result = chunk_result
                    best_length_rank = length_rank
            length_issue = ""
            if target and target[0] >= 100 and visible_length < target[0]:
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
            if len(chunk_text) >= 40 and not length_issue:
                break
            active_prompt = build_de_ai_chunk_repair_prompt(
                chunk_prompt,
                [{"kind": "length", "detail": length_issue}],
                previous_candidate=chunk_text,
            )
        if not best_chunk_text:
            raise RuntimeError(
                f"Revision model returned an unusable scene {chunk_index}/{len(chunk_prompts)}"
            )
        checkpoint_put("outputs", checkpoint_key, {
            "content": best_chunk_text,
            "model": best_chunk_result.get("model") or "",
            "request_meta": best_chunk_result.get("request_meta") or {},
        })
        return best_chunk_text, best_chunk_result

    indexed_prompts = list(enumerate(chunk_prompts, start=1))
    with ThreadPoolExecutor(
        max_workers=min(worker_limit, len(indexed_prompts)),
    ) as executor:
        chunk_outputs = list(executor.map(generate_chunk, indexed_prompts))
    chunk_texts = [item[0] for item in chunk_outputs]
    result = chunk_outputs[-1][1]

    def finalize_revision(
        fidelity_review: dict,
        style_review: dict,
    ) -> dict:
        result["content"] = "\n\n".join(chunk_texts)
        result["_chunk_lengths"] = [len(value) for value in chunk_texts]
        result["_story_ledger"] = story_ledger
        result["_fidelity_audit"] = fidelity_review
        result["_style_audit"] = style_review
        return result

    def audit_chunks(values: list[str]) -> dict:
        audit_system = (
            "你是小说事实校对员。只核对候选正文是否完整、准确保留原文故事，"
            "不评价文风，不重写正文。严格按用户要求只输出 JSON。"
        )
        audit_prompt = build_de_ai_fidelity_audit_prompt(authority, values)
        checkpoint_key = _content_hash(f"{audit_system}\n\n{audit_prompt}")
        cached = checkpoint_get("audits", checkpoint_key)
        if cached:
            parsed = parse_de_ai_fidelity_audit(
                cached.get("content"),
                chunk_count=len(values),
            )
            if parsed["valid"]:
                print("de-ai: restoring story-fidelity audit from checkpoint", flush=True)
                return parsed
        messages = [
            {"role": "system", "content": audit_system},
            {"role": "user", "content": audit_prompt},
        ]
        parsed: dict = {"valid": False, "passed": False, "issues": []}
        for protocol_attempt in range(1, 3):
            print(
                "de-ai: auditing story fidelity"
                + (" (JSON retry)" if protocol_attempt > 1 else ""),
                flush=True,
            )
            if direct_local_cli:
                audit_result = _direct_local_cli_chat(
                    base_url,
                    messages=messages,
                    model=model,
                    timeout_seconds=local_cli_timeout_seconds,
                )
            else:
                audit_result = _chat(
                    base_url,
                    messages=messages,
                    model=model,
                    temperature=0,
                )
            parsed = parse_de_ai_fidelity_audit(
                audit_result.get("content"),
                chunk_count=len(values),
            )
            if parsed["valid"]:
                checkpoint_put("audits", checkpoint_key, {
                    "content": str(audit_result.get("content") or "").strip(),
                })
                return parsed
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": str(audit_result.get("content") or "").strip(),
                },
                {
                    "role": "user",
                    "content": (
                        "上一条没有满足输出协议。不要重新分析或解释；请把同一审计结论"
                        "改成唯一一个合法 JSON 对象，且只含布尔 passed 与数组 issues。"
                    ),
                },
            ]
        return parsed

    def audit_style_chunks(values: list[str]) -> dict:
        audit_system = (
            "你是中文小说表达结构审计员。只识别成品化机器叙事结构，"
            "不核对故事事实，不重写正文。严格按用户要求只输出 JSON。"
        )
        audit_prompt = build_de_ai_style_audit_prompt(values)
        checkpoint_key = _content_hash(f"{audit_system}\n\n{audit_prompt}")
        cached = checkpoint_get("style_audits", checkpoint_key)
        if cached:
            parsed = parse_de_ai_style_audit(
                cached.get("content"),
                chunk_count=len(values),
            )
            if parsed["valid"]:
                print("de-ai: restoring expression-structure audit", flush=True)
                return parsed
        messages = [
            {"role": "system", "content": audit_system},
            {"role": "user", "content": audit_prompt},
        ]
        parsed: dict = {"valid": False, "passed": False, "issues": []}
        for protocol_attempt in range(1, 3):
            print(
                "de-ai: auditing expression structure"
                + (" (JSON retry)" if protocol_attempt > 1 else ""),
                flush=True,
            )
            if direct_local_cli:
                audit_result = _direct_local_cli_chat(
                    base_url,
                    messages=messages,
                    model=model,
                    timeout_seconds=local_cli_timeout_seconds,
                )
            else:
                audit_result = _chat(
                    base_url,
                    messages=messages,
                    model=model,
                    temperature=0,
                )
            parsed = parse_de_ai_style_audit(
                audit_result.get("content"),
                chunk_count=len(values),
            )
            if parsed["valid"]:
                checkpoint_put("style_audits", checkpoint_key, {
                    "content": str(audit_result.get("content") or "").strip(),
                })
                return parsed
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": str(audit_result.get("content") or "").strip(),
                },
                {
                    "role": "user",
                    "content": (
                        "上一条没有满足输出协议。不要重新分析或解释；请把同一审计结论"
                        "改成唯一一个合法 JSON 对象，且只含布尔 passed 与数组 issues。"
                    ),
                },
            ]
        return parsed

    def collect_repair_issues(audit: dict) -> dict[int, list[dict]]:
        collected: dict[int, list[dict]] = {}
        assessment = assess_de_ai_revision(
            authority,
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

    issue_history: dict[int, list[dict]] = {}
    def repair_fidelity(audit: dict) -> dict:
        nonlocal result
        if not audit["valid"]:
            raise RuntimeError("Revision model returned an unusable story-fidelity audit")
        pending_issues: dict[int, list[dict]] = {}
        for repair_attempt in range(1, 6):
            pending_issues = collect_repair_issues(audit)
            if not pending_issues:
                break
            current_issue_history = {
                chunk_index: [dict(issue) for issue in issues]
                for chunk_index, issues in pending_issues.items()
            }
            issue_history.update(current_issue_history)
            print(
                f"de-ai: semantic repair {repair_attempt}/5 for scene(s) "
                + ", ".join(str(value) for value in sorted(pending_issues)),
                flush=True,
            )
            repair_items = []
            for chunk_index in sorted(pending_issues):
                history = current_issue_history[chunk_index]
                if any(
                    str(item.get("kind") or "").startswith("style:")
                    for item in history
                ):
                    repair_prompt = build_de_ai_style_repair_prompt(
                        chunk_prompts[chunk_index - 1],
                        history,
                        repair_attempt=repair_attempt,
                        previous_candidate=chunk_texts[chunk_index - 1],
                        fidelity_chunk_prompt=(
                            detailed_chunk_prompts[chunk_index - 1]
                        ),
                    )
                else:
                    repair_prompt = build_de_ai_chunk_repair_prompt(
                        detailed_chunk_prompts[chunk_index - 1],
                        history,
                        repair_attempt=repair_attempt,
                        previous_candidate=chunk_texts[chunk_index - 1],
                    )
                repair_items.append((chunk_index, repair_prompt))
            with ThreadPoolExecutor(
                max_workers=min(worker_limit, len(repair_items)),
            ) as executor:
                repair_outputs = list(executor.map(generate_chunk, repair_items))
            for chunk_index, repair_output in zip(
                sorted(pending_issues),
                repair_outputs,
                strict=True,
            ):
                chunk_texts[chunk_index - 1] = repair_output[0]
            result = repair_outputs[-1][1]
            audit = audit_chunks(chunk_texts)
            if not audit["valid"]:
                raise RuntimeError("Revision model returned an unusable repaired audit")

        pending_issues = collect_repair_issues(audit)
        if pending_issues or not audit["passed"]:
            details = "; ".join(
                str(item.get("detail") or "")
                for values in pending_issues.values()
                for item in values
                if str(item.get("detail") or "")
            )
            raise RuntimeError(
                "Repaired candidate still failed story-fidelity audit"
                + (f": {details}" if details else "")
            )
        return audit

    try:
        fidelity_audit = repair_fidelity(audit_chunks(chunk_texts))
    except Exception as exc:
        fidelity_audit = _runtime_audit_failure("故事保真审计", exc)
        style_audit = _runtime_audit_failure(
            "表达结构审计",
            "故事保真审计不可用，未继续消耗审计调用",
        )
        return finalize_revision(fidelity_audit, style_audit)

    try:
        style_audit = audit_style_chunks(chunk_texts)
    except Exception as exc:
        style_audit = _runtime_audit_failure("表达结构审计", exc)
        return finalize_revision(fidelity_audit, style_audit)
    if not style_audit["valid"]:
        raise RuntimeError("Revision model returned an unusable expression audit")
    style_issue_history: dict[int, list[dict]] = {}

    def style_candidate_score(
        values: list[str],
        audit: dict,
    ) -> tuple[int, int, int, int, int, int, int]:
        assessment = assess_de_ai_revision(
            authority,
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
            0 if audit.get("valid") and audit.get("passed") and not issues else 1,
            issue_count,
            issue_severity,
            novel_kinds,
            novel_pairs,
            # Match the production path: once the revision and fidelity floors
            # pass, a tighter branch is less likely to retain the staged or
            # checklist padding this repair pass is meant to remove.
            count_de_ai_visible_characters("\n\n".join(values)),
        )

    best_chunk_texts = list(chunk_texts)
    best_fidelity_audit = fidelity_audit
    best_style_audit = style_audit
    best_style_score = style_candidate_score(chunk_texts, style_audit)
    for style_attempt in range(1, DE_AI_STRUCTURAL_REPAIR_ATTEMPTS + 1):
        pending_style_issues: dict[int, list[dict]] = {}
        for issue in style_audit.get("issues", []):
            chunk_index = int(issue["chunk"])
            pending_style_issues.setdefault(chunk_index, []).append(issue)
        if not pending_style_issues:
            break
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
        # Keep the baseline and repaired branch on the same novelty basis.
        # Otherwise the baseline retains the score assigned before history was
        # populated and a repair with identical defects wins artificially.
        best_style_score = style_candidate_score(
            best_chunk_texts,
            best_style_audit,
        )
        print(
            "de-ai: structural repair "
            f"{style_attempt}/{DE_AI_STRUCTURAL_REPAIR_ATTEMPTS} for scene(s) "
            + ", ".join(str(value) for value in sorted(pending_style_issues)),
            flush=True,
        )
        chapter_floor = (
            max(2_000, round(count_de_ai_visible_characters(authority) * 0.95))
            if count_de_ai_visible_characters(authority) >= 2_000
            else round(count_de_ai_visible_characters(authority) * 0.9)
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
        repair_items = [
            (
                chunk_index,
                build_de_ai_style_repair_prompt(
                    chunk_prompts[chunk_index - 1],
                    style_issue_history[chunk_index],
                    repair_attempt=style_attempt,
                    allow_target_shrink=False,
                    minimum_target_characters=round(
                        remaining_floor
                        * count_de_ai_visible_characters(
                            chunk_texts[chunk_index - 1]
                        )
                        / max(1, repair_visible)
                    ),
                    previous_candidate=chunk_texts[chunk_index - 1],
                    fidelity_chunk_prompt=(
                        detailed_chunk_prompts[chunk_index - 1]
                    ),
                ),
                DE_AI_STRUCTURAL_OUTPUT_ATTEMPTS,
            )
            for chunk_index in sorted(pending_style_issues)
        ]
        with ThreadPoolExecutor(
            max_workers=min(worker_limit, len(repair_items)),
        ) as executor:
            repair_outputs = list(executor.map(generate_chunk, repair_items))
        for chunk_index, repair_output in zip(
            sorted(pending_style_issues),
            repair_outputs,
            strict=True,
        ):
            chunk_texts[chunk_index - 1] = repair_output[0]
        result = repair_outputs[-1][1]
        branch_fidelity_audit = audit_chunks(chunk_texts)
        branch_assessment = assess_de_ai_revision(
            authority,
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
        fidelity_audit = repair_fidelity(branch_fidelity_audit)
        style_audit = audit_style_chunks(chunk_texts)
        if not style_audit["valid"]:
            raise RuntimeError("Revision model returned an unusable repaired expression audit")
        candidate_score = style_candidate_score(chunk_texts, style_audit)
        if candidate_score < best_style_score:
            best_chunk_texts = list(chunk_texts)
            best_fidelity_audit = fidelity_audit
            best_style_audit = style_audit
            best_style_score = candidate_score

    if style_audit.get("issues") or not style_audit["passed"]:
        chunk_texts = best_chunk_texts
        fidelity_audit = best_fidelity_audit
        style_audit = {
            **best_style_audit,
            "exhausted": True,
            "selected_best": True,
            "repair_attempts": DE_AI_STRUCTURAL_REPAIR_ATTEMPTS,
        }

    return finalize_revision(fidelity_audit, style_audit)


def _visible_length(value: str) -> int:
    return count_de_ai_visible_characters(value)


def _assess_lineage_round(
    authority_source: str,
    round_input: str,
    candidate: str,
    *,
    min_length_ratio: float = 0.9,
    require_substantial_revision: bool = True,
) -> dict:
    """Audit safety against round one and transformation against this round.

    Follow-up candidates may contain wording (or even an erroneous literal)
    introduced by an earlier model turn.  Such material must never become a
    new story requirement.  The current input is therefore consulted only for
    the high-confidence no-op check; all length, fact-token, dialogue, and
    wrapper guards remain anchored to the immutable first-round source.
    """

    assessment = assess_de_ai_revision(
        authority_source,
        candidate,
        min_length_ratio=min_length_ratio,
        require_substantial_revision=False,
    )
    input_assessment = assess_de_ai_revision(
        round_input,
        candidate,
        min_length_ratio=min_length_ratio,
        require_substantial_revision=require_substantial_revision,
    )
    existing_codes = {
        str(issue.get("code") or "")
        for issue in assessment.get("issues", [])
        if isinstance(issue, dict)
    }
    for issue in input_assessment.get("issues", []):
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "")
        if code == "insufficient_revision" and code not in existing_codes:
            assessment["issues"].append(dict(issue))
            existing_codes.add(code)
    assessment["accepted"] = not assessment.get("issues")
    assessment["round_input_similarity"] = input_assessment.get(
        "source_similarity"
    )
    assessment["round_input_visible_characters"] = input_assessment.get(
        "original_visible_characters"
    )
    return assessment


def _normalize_stdin_source(value: str) -> str:
    """Undo Windows native-pipe framing without changing the prose itself."""

    return value.replace("\r\n", "\n").removeprefix("\ufeff").rstrip("\n")


def _feedback_length_repair_attempt_limit(repair_span_count: int) -> int:
    """Allow safe partial insertions to converge without looping forever."""

    return max(4, max(0, repair_span_count) * 4)


def _scope_style_audit_to_rejected_spans(
    audit: dict,
    repair_indexes: list[int],
) -> dict:
    """Keep externally accepted spans locked and out of local style scoring."""

    if not audit.get("valid"):
        return audit
    repair_chunks = {index + 1 for index in repair_indexes}
    issues = list(audit.get("issues") or [])
    relevant = [item for item in issues if int(item.get("chunk") or 0) in repair_chunks]
    ignored = [item for item in issues if int(item.get("chunk") or 0) not in repair_chunks]
    return {
        **audit,
        "passed": not relevant,
        "issues": relevant,
        "ignored_detector_human_span_issues": ignored,
    }


def _map_local_style_audit_to_source_chunks(
    audit: dict,
    repair_indexes: list[int],
) -> dict:
    """Map an audit of rejected spans only back onto whole-source chunks."""

    if not audit.get("valid"):
        return audit
    mapped: list[dict] = []
    for item in audit.get("issues") or []:
        local_chunk = int(item.get("chunk") or 0)
        if not 1 <= local_chunk <= len(repair_indexes):
            return {"valid": False, "passed": False, "issues": []}
        mapped.append({
            **item,
            "chunk": repair_indexes[local_chunk - 1] + 1,
        })
    return {
        **audit,
        "passed": not mapped,
        "issues": mapped,
        "audited_detector_rejected_spans_only": True,
    }


def _parse_detector_verdicts(value: str, source: str) -> list[dict]:
    """Parse verdict:length pairs into exact, contiguous source spans."""

    aliases = {
        "human": "human",
        "success": "human",
        "suspected": "suspected",
        "warning": "suspected",
        "ai": "ai",
        "danger": "ai",
    }
    spans: list[dict] = []
    offset = 0
    for raw_item in str(value or "").split(","):
        item = raw_item.strip()
        if not item:
            continue
        verdict_value, separator, length_value = item.partition(":")
        verdict = aliases.get(verdict_value.strip().lower())
        if not separator or verdict is None:
            raise ValueError(
                "Detector verdicts must use verdict:length with human, suspected, or ai"
            )
        try:
            length = int(length_value.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid detector span length: {length_value}") from exc
        if length <= 0:
            raise ValueError("Detector span lengths must be positive")
        spans.append({
            "start": offset,
            "end": offset + length,
            "verdict": verdict,
        })
        offset += length
    if not spans or offset != len(source):
        raise ValueError(
            f"Detector spans cover {offset} characters, but source has {len(source)}"
        )
    if all(item["verdict"] == "human" for item in spans):
        raise ValueError("Detector feedback has no span that needs repair")
    return spans


def _clean_generated_text(value: object) -> str:
    text_value = str(value or "").strip()
    if not text_value.startswith("```"):
        return text_value
    lines = text_value.splitlines()[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _runtime_audit_failure(label: str, exc: Exception | str) -> dict:
    """Preserve a generated candidate when a review model is unavailable."""

    detail = str(exc).strip() or "unknown review failure"
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


def _feedback_revise(
    base_url: str,
    model: str,
    source: str,
    spans: list[dict],
    *,
    audit_model: str | None = None,
    fidelity_repair_model: str | None = None,
    style_repair_model: str | None = None,
    length_repair_model: str | None = None,
    direct_local_cli: bool,
    checkpoint_path: Path | None = None,
    local_cli_timeout_seconds: int = 900,
    structural_repair_attempts: int = 3,
    minimum_output_visible_characters: int = 0,
) -> dict:
    """Regenerate only detector-rejected spans, then audit the assembled chapter."""

    effective_audit_model = str(audit_model or model)
    effective_fidelity_repair_model = str(fidelity_repair_model or model)
    effective_style_repair_model = str(style_repair_model or model)
    effective_length_repair_model = str(
        length_repair_model or effective_fidelity_repair_model
    )
    checkpoint_identity_parts = [model]
    if effective_audit_model != model:
        checkpoint_identity_parts.append(f"audit={effective_audit_model}")
    if effective_fidelity_repair_model != model:
        checkpoint_identity_parts.append(
            f"fidelity-repair={effective_fidelity_repair_model}"
        )
    checkpoint_identity = "|".join(checkpoint_identity_parts)
    checkpoint = _load_checkpoint(
        checkpoint_path,
        source=source,
        model=checkpoint_identity,
    )
    checkpoint.setdefault("feedback_ledgers", {})
    checkpoint.setdefault("feedback_compact_ledgers", {})
    checkpoint_lock = Lock()
    segments = [source[item["start"]:item["end"]] for item in spans]
    rejected_visible_total = sum(
        _visible_length(segment)
        for segment, span in zip(segments, spans, strict=True)
        if span["verdict"] != "human"
    )
    preserved_visible_total = sum(
        _visible_length(segment)
        for segment, span in zip(segments, spans, strict=True)
        if span["verdict"] == "human"
    )
    required_rejected_visible_total = max(
        0,
        int(minimum_output_visible_characters or 0) - preserved_visible_total,
    )
    model_name = str(model or "")
    if model_name.startswith("opencode_cli:"):
        worker_limit = 1
    elif model_name.startswith("codex_cli:"):
        worker_limit = 2
    else:
        worker_limit = 3

    def checkpoint_get(section: str, key: str) -> dict | None:
        with checkpoint_lock:
            values = checkpoint.get(section)
            if not isinstance(values, dict):
                return None
            item = values.get(key)
            return dict(item) if isinstance(item, dict) else None

    def checkpoint_put(section: str, key: str, item: dict) -> None:
        with checkpoint_lock:
            checkpoint.setdefault(section, {})[key] = item
            _write_checkpoint(checkpoint_path, checkpoint)

    def chat(
        messages: list[dict],
        *,
        temperature: float,
        for_audit: bool = False,
        model_override: str | None = None,
    ) -> dict:
        selected_model = str(
            model_override
            or (effective_audit_model if for_audit else model)
        )
        if direct_local_cli:
            return _direct_local_cli_chat(
                base_url,
                messages=messages,
                model=selected_model,
                timeout_seconds=local_cli_timeout_seconds,
            )
        return _chat(
            base_url,
            messages=messages,
            model=selected_model,
            temperature=temperature,
        )

    system = (
        "你是中文小说的重写编辑。只能依据事实账本重生被检测退回的连续区段，"
        "不得改写已保留的相邻正文，不得新增或删改故事事实。"
        "直接输出小说正文，不要标题、说明、清单、Markdown 或检测报告。\n\n"
        f"【作品文风】\n{_style_context()}"
    )

    ledgers: dict[int, str] = {}

    def ledger_for(index: int) -> str:
        segment = segments[index]
        key = _content_hash(f"feedback-ledger\n{segment}")
        cached = checkpoint_get("feedback_ledgers", key)
        if cached and len(str(cached.get("content") or "").strip()) >= 20:
            print(f"de-ai: restoring detector ledger {index + 1}/{len(spans)}", flush=True)
            detailed_ledger = str(cached["content"]).strip()
        else:
            print(f"de-ai: extracting detector ledger {index + 1}/{len(spans)}", flush=True)
            result = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是小说事实记录员。只抽取本区段已有事实、事件顺序、线索、"
                            "物件归属和对白意图；省略对前文的重复复盘，不写小说正文。"
                        ),
                    },
                    {"role": "user", "content": build_de_ai_story_ledger_prompt(segment)},
                ],
                temperature=0.1,
                for_audit=True,
            )
            detailed_ledger = _clean_generated_text(result.get("content"))
            minimum = min(80, max(20, len(segment) // 4))
            if len(detailed_ledger) < minimum:
                raise RuntimeError(f"Detector ledger {index + 1} is unusable")
            checkpoint_put("feedback_ledgers", key, {
                "content": detailed_ledger,
                "model": result.get("model") or "",
                "request_meta": result.get("request_meta") or {},
            })

        compact_prompt = build_de_ai_detector_ledger_compression_prompt(
            segment,
            detailed_ledger,
            is_ending=index + 1 == len(spans),
            preserved_context=(
                (segments[index - 1] if index > 0 else "")
                + (segments[index + 1] if index + 1 < len(segments) else "")
            ),
        )
        compact_key = _content_hash(f"feedback-compact-ledger-v1\n{compact_prompt}")
        cached_compact = checkpoint_get("feedback_compact_ledgers", compact_key)
        if cached_compact and len(str(cached_compact.get("content") or "").strip()) >= 40:
            print(
                f"de-ai: restoring compact detector ledger {index + 1}/{len(spans)}",
                flush=True,
            )
            return str(cached_compact["content"]).strip()
        print(f"de-ai: compressing detector ledger {index + 1}/{len(spans)}", flush=True)
        compact_result = chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是小说事实账本压缩员。合并微动作、删除重复复盘，"
                        "同时保留人物、数字、条件、因果、对白信息和先后。"
                        "只输出用户要求的短账本，不写小说正文。"
                    ),
                },
                {"role": "user", "content": compact_prompt},
            ],
            temperature=0,
            for_audit=True,
        )
        compact_ledger = _clean_generated_text(compact_result.get("content"))
        if len(compact_ledger) < 40 or "[硬]" not in compact_ledger:
            raise RuntimeError(f"Compact detector ledger {index + 1} is unusable")
        checkpoint_put("feedback_compact_ledgers", compact_key, {
            "content": compact_ledger,
            "model": compact_result.get("model") or "",
            "request_meta": compact_result.get("request_meta") or {},
        })
        return compact_ledger

    def rewrite_segment(
        index: int,
        *,
        fidelity_issues: list[dict] | None = None,
        style_issues: list[dict] | None = None,
        previous_candidate: str = "",
        pass_number: int = 1,
        minimum_visible_override: int = 0,
    ) -> tuple[str, dict]:
        span = spans[index]
        if span["verdict"] == "human":
            return segments[index], {}
        ledger = ledgers.get(index) or ledger_for(index)
        ledgers[index] = ledger
        left = segments[index - 1][-600:] if index > 0 else ""
        right = segments[index + 1][:600] if index + 1 < len(segments) else ""
        segment_visible = _visible_length(segments[index])
        minimum_segment_visible = 0
        if rejected_visible_total and required_rejected_visible_total:
            minimum_segment_visible = (
                required_rejected_visible_total * segment_visible
                + rejected_visible_total
                - 1
            ) // rejected_visible_total
        minimum_segment_visible = max(
            minimum_segment_visible,
            max(0, int(minimum_visible_override or 0)),
        )
        prompt = build_de_ai_detector_feedback_repair_prompt(
            segments[index],
            ledger,
            left_context=left,
            right_context=right,
            verdict=span["verdict"],
            pass_number=pass_number,
            minimum_visible_characters=minimum_segment_visible,
        )
        if style_issues:
            prompt = build_de_ai_style_repair_prompt(
                prompt,
                style_issues,
                repair_attempt=pass_number,
                allow_target_shrink=False,
            )
        if fidelity_issues:
            prompt = build_de_ai_chunk_repair_prompt(
                prompt,
                fidelity_issues,
                repair_attempt=pass_number,
                previous_candidate=previous_candidate,
            )
        key = _content_hash(f"{system}\n\n{prompt}")
        target = parse_de_ai_chunk_target(prompt)
        cached = checkpoint_get("outputs", key)
        cached_text = str((cached or {}).get("content") or "").strip()
        writer_model = (
            effective_fidelity_repair_model
            if fidelity_issues
            else effective_style_repair_model
            if style_issues
            else model
        )
        expected_cached_model = writer_model.split(":", 1)[-1]
        cached_model = str((cached or {}).get("model") or "").strip()
        cached_model_valid = (
            not cached_model
            or cached_model == expected_cached_model
        )
        # A cached value is written only after the bounded three-attempt loop
        # has completed.  Its final attempt is intentionally allowed to make
        # safe partial progress even when the prose model undershoots the
        # segment target.  Reapplying the first-draft length gate here makes a
        # resumed run regenerate the same span forever.  Whole-story lineage
        # length and deterministic/fidelity audits still run below.
        cached_length_valid = True
        if (
            cached
            and cached_model_valid
            and len(cached_text) >= 40
            and cached_length_valid
        ):
            print(f"de-ai: restoring detector repair {index + 1}/{len(spans)}", flush=True)
            return cached_text, cached
        output = ""
        result: dict = {}
        active_prompt = prompt
        for attempt in range(1, 4):
            print(
                f"de-ai: regenerating detector span {index + 1}/{len(spans)}"
                + (f" (retry {attempt})" if attempt > 1 else ""),
                flush=True,
            )
            result = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": active_prompt},
                ],
                temperature=0.95,
                model_override=writer_model,
            )
            output = _clean_generated_text(result.get("content"))
            visible = _visible_length(output)
            length_issue = ""
            if target and visible < round(target[0] * 0.95):
                length_issue = (
                    f"上一稿只有约{visible}个可见字符，短于本段目标"
                    f"{target[0]}至{target[1]}字。用账本已有现场动作和对白补足，"
                    "不得复盘或新增事实。"
                )
            elif target and visible > round(target[1] * 1.3):
                length_issue = (
                    f"上一稿约{visible}个可见字符，超过本段目标"
                    f"{target[0]}至{target[1]}字。删掉解释和重复步骤，不得删除硬事实。"
                )
            if len(output) >= 40 and (not length_issue or attempt == 3):
                break
            active_prompt = build_de_ai_chunk_repair_prompt(
                prompt,
                [{
                    "kind": "length" if length_issue else "output",
                    "detail": length_issue or "模型没有输出可用正文",
                }],
                repair_attempt=attempt + 1,
                previous_candidate=output or previous_candidate,
            )
        if len(output) < 40:
            raise RuntimeError(f"Detector repair {index + 1} is unusable")
        cached = {
            "content": output,
            "model": result.get("model") or "",
            "request_meta": result.get("request_meta") or {},
        }
        checkpoint_put("outputs", key, cached)
        return output, cached

    repair_indexes = [
        index for index, item in enumerate(spans) if item["verdict"] != "human"
    ]
    with ThreadPoolExecutor(max_workers=min(worker_limit, len(repair_indexes))) as executor:
        generated = list(executor.map(ledger_for, repair_indexes))
    for index, ledger in zip(repair_indexes, generated, strict=True):
        ledgers[index] = ledger
    if worker_limit == 1:
        # Do not pre-schedule later model calls for single-worker providers.  A
        # rejected/empty earlier span must stop the run before another slow CLI
        # request starts, while multi-worker providers can still run in parallel.
        generated_repairs = [rewrite_segment(index) for index in repair_indexes]
    else:
        with ThreadPoolExecutor(max_workers=min(worker_limit, len(repair_indexes))) as executor:
            generated_repairs = list(executor.map(rewrite_segment, repair_indexes))
    chunk_texts = list(segments)
    result: dict = {}
    for index, repair in zip(repair_indexes, generated_repairs, strict=True):
        chunk_texts[index] = repair[0]
        result = repair[1]

    fidelity_issue_history = _restore_fidelity_issue_history(
        checkpoint.get("audits"),
        chunk_count=len(segments),
    )

    def remember_fidelity_issues(audit: dict) -> None:
        for issue in audit.get("issues", []):
            index = int(issue.get("chunk") or 0) - 1
            if index not in repair_indexes:
                continue
            history = fidelity_issue_history.setdefault(index, [])
            identity = (
                str(issue.get("kind") or ""),
                str(issue.get("detail") or ""),
            )
            if identity not in {
                (str(item.get("kind") or ""), str(item.get("detail") or ""))
                for item in history
            }:
                history.append(dict(issue))

    def all_fidelity_issue_history() -> list[dict]:
        return [
            item
            for index in sorted(fidelity_issue_history)
            for item in fidelity_issue_history[index]
        ]

    def audit_fidelity(
        values: list[str],
        *,
        focus_issues: list[dict] | None = None,
    ) -> dict:
        prompt = build_de_ai_fidelity_audit_prompt(source, values)
        if focus_issues:
            focus_lines = "\n".join(
                f"- 第{int(item.get('chunk') or 0)}段：{str(item.get('detail') or '')}"
                for item in focus_issues
                if str(item.get("detail") or "").strip()
            )
            if focus_lines:
                prompt += (
                    "\n\n【必须逐项复核的历史错误】\n"
                    "下列错误曾在较早候选中出现。即使本稿文字更流畅，也必须明确检查是否复发；"
                    "复发则 passed=false。\n"
                    + focus_lines
                )
        key = _content_hash(f"feedback-fidelity\n{prompt}")
        cached = checkpoint_get("audits", key)
        if cached:
            audit_text = str(cached.get("content") or "")
        else:
            print("de-ai: auditing detector-guided story fidelity", flush=True)
            audit_result = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是小说事实校对员。只核对候选正文是否完整、准确保留原文故事，"
                            "不评价文风，不重写正文。严格按用户要求只输出 JSON。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                for_audit=True,
            )
            audit_text = str(audit_result.get("content") or "")
            checkpoint_put("audits", key, {"content": audit_text})
        return parse_de_ai_fidelity_audit(audit_text, chunk_count=len(values))

    fidelity_audit = audit_fidelity(chunk_texts)
    remember_fidelity_issues(fidelity_audit)
    # Match the public workflow's bounded multi-repair behavior.  A newly
    # discovered order/ownership regression should still get one complete
    # model regeneration after two earlier fact repairs, without hand-patching
    # the prose or accepting the last failed candidate.
    for audit_pass in range(2, 2 + _DETECTOR_FIDELITY_REPAIR_ATTEMPTS):
        if fidelity_audit["valid"] and fidelity_audit["passed"]:
            break
        grouped: dict[int, list[dict]] = {}
        for issue in fidelity_audit.get("issues", []):
            index = int(issue.get("chunk") or 0) - 1
            if index in repair_indexes:
                grouped.setdefault(index, []).append(issue)
        if not grouped:
            break
        for index, issues in grouped.items():
            repaired, repair_meta = rewrite_segment(
                index,
                fidelity_issues=issues,
                previous_candidate=chunk_texts[index],
                pass_number=audit_pass,
            )
            chunk_texts[index] = repaired
            result = repair_meta
        fidelity_audit = audit_fidelity(
            chunk_texts,
            focus_issues=all_fidelity_issue_history(),
        )
        remember_fidelity_issues(fidelity_audit)
    if not fidelity_audit["valid"] or not fidelity_audit["passed"]:
        details = "; ".join(
            str(item.get("detail") or "") for item in fidelity_audit.get("issues", [])
        )
        raise RuntimeError(f"Detector-guided candidate failed fidelity audit: {details}")

    # A fact repair can legitimately shorten a span by a few characters.  Do
    # not patch prose in code or regenerate the entire audited span.  Ask the
    # prose model for an insertion-only expansion, validate that every existing
    # non-whitespace character remains in order, then audit the whole story.
    def expand_audited_segment(
        index: int,
        *,
        minimum_visible: int,
        maximum_visible: int,
        required_insertions: list[str] | None = None,
    ) -> tuple[str, dict]:
        previous = chunk_texts[index]
        prompt = build_de_ai_candidate_preserving_expansion_prompt(
            previous,
            ledgers[index],
            minimum_visible_characters=minimum_visible,
            maximum_visible_characters=maximum_visible,
            required_insertions=required_insertions,
        )
        system_prompt = (
            "你是中文小说保真扩写编辑。候选正文已经通过事实审计；严格执行字符保留规则，"
            "只能插入账本已有信息。直接输出完整小说片段。"
        )
        key = _content_hash(
            f"length-model={effective_length_repair_model}\n"
            f"{system_prompt}\n\n{prompt}"
        )
        cached = checkpoint_get("outputs", key)
        cached_text = _clean_generated_text((cached or {}).get("content"))

        def validate(value: str) -> list[str]:
            issues: list[str] = []
            visible = _visible_length(value)
            if visible < minimum_visible:
                issues.append(f"只有{visible}个可见字符，至少需要{minimum_visible}个")
            if visible > maximum_visible:
                issues.append(f"有{visible}个可见字符，最多允许{maximum_visible}个")
            if not _preserves_non_whitespace_characters(previous, value):
                issues.append("删除、替换或调换了候选中的原有字符")
            for token in required_insertions or []:
                if token not in value:
                    issues.append(f"仍未补回源文事实标记：{token}")
            return issues

        if cached_text and not validate(cached_text):
            print(
                f"de-ai: restoring insertion-only length repair {index + 1}/{len(spans)}",
                flush=True,
            )
            return cached_text, dict(cached or {})
        active_prompt = prompt
        output = ""
        result: dict = {}
        validation_issues: list[str] = []
        best_partial_output = ""
        best_partial_result: dict = {}
        for attempt in range(1, 4):
            print(
                f"de-ai: insertion-only length repair {index + 1}/{len(spans)}"
                + (f" (retry {attempt})" if attempt > 1 else ""),
                flush=True,
            )
            try:
                result = chat(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": active_prompt},
                    ],
                    temperature=0.45,
                    model_override=effective_length_repair_model,
                )
            except Exception as exc:  # noqa: BLE001 - isolate one external CLI attempt
                validation_issues = [f"模型调用失败：{exc}"]
                active_prompt = "\n\n".join([
                    prompt,
                    "【上一稿未通过确定性校验】\n- "
                    + "\n- ".join(validation_issues),
                    "重新输出。必须逐字保留不可删除候选的全部非空白字符，"
                    "仅插入少量账本已有内容。",
                ])
                continue
            output = _clean_generated_text(result.get("content"))
            validation_issues = validate(output)
            if output and not validation_issues:
                break
            visible = _visible_length(output)
            non_length_issues = [
                issue
                for issue in validation_issues
                if not (
                    issue.startswith("只有")
                    and "个可见字符，至少需要" in issue
                )
            ]
            if (
                output
                and visible > _visible_length(previous)
                and visible <= maximum_visible
                and not non_length_issues
                and visible > _visible_length(best_partial_output)
            ):
                best_partial_output = output
                best_partial_result = dict(result)
            active_prompt = "\n\n".join([
                prompt,
                "【上一稿未通过确定性校验】\n- " + "\n- ".join(validation_issues),
                "重新输出。必须逐字保留不可删除候选的全部非空白字符，仅插入少量账本已有内容。",
            ])
        if validation_issues and best_partial_output:
            output = best_partial_output
            result = best_partial_result
            validation_issues = []
            print(
                "de-ai: accepting safe partial length progress for detector span "
                f"{index + 1}/{len(spans)}",
                flush=True,
            )
        if not output or validation_issues:
            raise RuntimeError(
                "Insertion-only length repair is unusable: "
                + "; ".join(validation_issues or ["empty output"])
            )
        cached = {
            "content": output,
            "model": result.get("model") or "",
            "request_meta": result.get("request_meta") or {},
        }
        checkpoint_put("outputs", key, cached)
        return output, cached

    tried_length_indexes: set[int] = set()
    stalled_length_passes = 0
    for length_pass in range(
        1,
        _feedback_length_repair_attempt_limit(len(repair_indexes)) + 1,
    ):
        candidate_visible = _visible_length("".join(chunk_texts))
        deterministic_preflight = assess_de_ai_revision(
            source,
            "".join(chunk_texts),
            min_length_ratio=0.88,
            require_substantial_revision=False,
        )
        missing_tokens = list(
            deterministic_preflight.get("missing_protected_tokens") or []
        )
        if (
            (
                not minimum_output_visible_characters
                or candidate_visible >= minimum_output_visible_characters
            )
            and not missing_tokens
        ):
            break
        deficit = max(
            0,
            int(minimum_output_visible_characters or 0) - candidate_visible,
        )
        available_indexes = [
            index for index in repair_indexes if index not in tried_length_indexes
        ] or list(repair_indexes)
        missing_by_index = {
            index: [
                token
                for token in missing_tokens
                if token in segments[index] and token not in chunk_texts[index]
            ]
            for index in available_indexes
        }
        index = max(
            available_indexes,
            key=lambda value: (
                bool(missing_by_index[value]),
                _visible_length(chunk_texts[value]),
                _visible_length(segments[value]),
            ),
        )
        tried_length_indexes.add(index)
        current_visible = _visible_length(chunk_texts[index])
        required_insertions = missing_by_index[index]
        required_growth = max(
            deficit,
            sum(_visible_length(token) for token in required_insertions),
            1,
        )
        # Require exactly the remaining whole-chapter deficit.  The insertion
        # guard already prevents shrinkage, so extra headroom only causes valid
        # minimal expansions to be rejected and encourages unnecessary prose.
        requested_visible = current_visible + required_growth
        maximum_visible = requested_visible + 36
        print(
            "de-ai: restoring feedback-chain length floor in detector span "
            f"{index + 1}/{len(spans)} (need {deficit} more visible characters)",
            flush=True,
        )
        previous_text = chunk_texts[index]
        try:
            repaired, repair_meta = expand_audited_segment(
                index,
                minimum_visible=requested_visible,
                maximum_visible=maximum_visible,
                required_insertions=required_insertions,
            )
        except RuntimeError as exc:
            print(f"de-ai: insertion-only length repair skipped: {exc}", flush=True)
            stalled_length_passes += 1
            if stalled_length_passes >= len(repair_indexes):
                break
            continue
        chunk_texts[index] = repaired
        candidate_fidelity = audit_fidelity(chunk_texts)
        relevant_fidelity_issues = [
            issue
            for issue in candidate_fidelity.get("issues", [])
            if int(issue.get("chunk") or 0) - 1 == index
        ]
        if candidate_fidelity["valid"] and (
            candidate_fidelity["passed"] or not relevant_fidelity_issues
        ):
            fidelity_audit = (
                candidate_fidelity
                if candidate_fidelity["passed"]
                else {
                    "valid": True,
                    "passed": True,
                    "issues": [],
                    "incremental_scope": index + 1,
                    "ignored_unchanged_chunk_issues": candidate_fidelity.get(
                        "issues", []
                    ),
                }
            )
            result = repair_meta
            if _visible_length("".join(chunk_texts)) > candidate_visible:
                stalled_length_passes = 0
            else:
                stalled_length_passes += 1
                if stalled_length_passes >= len(repair_indexes):
                    break
            continue
        # The pre-expansion candidate already passed fidelity.  Revert rather
        # than trading a length improvement for a story change, then try a
        # different detector-rejected span once.
        chunk_texts[index] = previous_text
        print(
            "de-ai: rejecting length-floor repair because fidelity changed",
            flush=True,
        )
        stalled_length_passes += 1
        if stalled_length_passes >= len(repair_indexes):
            break

    def audit_style(values: list[str]) -> dict:
        # Detector-human spans are immutable in this workflow.  Do not expose
        # them to the local structure auditor: a whole-chapter audit can quote
        # a preamble/checklist from a locked neighbour yet assign it to the
        # only editable chunk, sending repair attempts to the wrong text.  The
        # final Zhuque result remains the whole assembled chapter authority.
        audit_values = [values[index] for index in repair_indexes]
        prompt = build_de_ai_style_audit_prompt(audit_values)
        key = _content_hash(f"feedback-style\n{prompt}")
        cached = checkpoint_get("style_audits", key)
        if cached:
            audit_text = str(cached.get("content") or "")
        else:
            print("de-ai: auditing detector-guided expression structure", flush=True)
            style_result = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是中文小说表达结构审计员。只识别高置信的成品化机器叙事结构，"
                            "不核对事实、不重写正文。严格只输出 JSON。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                for_audit=True,
            )
            audit_text = str(style_result.get("content") or "")
            checkpoint_put("style_audits", key, {"content": audit_text})
        parsed = parse_de_ai_style_audit(
            audit_text,
            chunk_count=len(audit_values),
        )
        return _map_local_style_audit_to_source_chunks(parsed, repair_indexes)

    def style_score(values: list[str], audit: dict) -> tuple[int, int, int, int]:
        weights = {
            "recap": 3,
            "exposition": 3,
            "preamble": 3,
            "staged": 3,
            "uniform": 3,
            "camera": 2,
            "stock": 2,
            "checklist": 1,
        }
        assessment = assess_de_ai_revision(
            source,
            "".join(values),
            min_length_ratio=0.88,
            require_substantial_revision=False,
        )
        below_lineage_floor = bool(
            minimum_output_visible_characters
            and _visible_length("".join(values)) < minimum_output_visible_characters
        )
        issues = audit.get("issues", [])
        return (
            0 if assessment["accepted"] and not below_lineage_floor else 1,
            len(issues),
            sum(weights.get(str(item.get("kind") or ""), 2) for item in issues),
            # Once deterministic length and fidelity floors pass, a tighter
            # candidate is the safer tie-break for staged/checklist/recap
            # repairs. Preferring the longest branch silently selects the
            # very step-by-step expansion the structural pass was removing.
            _visible_length("".join(values)),
        )

    style_audit = audit_style(chunk_texts)
    best_chunk_texts = list(chunk_texts)
    best_fidelity_audit = fidelity_audit
    best_style_audit = style_audit
    best_score = style_score(chunk_texts, style_audit)
    style_issue_history: dict[int, list[dict]] = {}
    for style_pass in range(1, max(0, structural_repair_attempts) + 1):
        grouped: dict[int, list[dict]] = {}
        for issue in style_audit.get("issues", []):
            index = int(issue.get("chunk") or 0) - 1
            if index in repair_indexes:
                grouped.setdefault(index, []).append(issue)
        if not grouped:
            break
        for index, issues in grouped.items():
            history = style_issue_history.setdefault(index, [])
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
        print(
            f"de-ai: detector structural repair {style_pass}/3 for span(s) "
            + ", ".join(str(index + 1) for index in sorted(grouped)),
            flush=True,
        )
        repair_items = sorted(grouped)

        def repair_style(
            index: int,
            *,
            active_style_pass: int = style_pass,
        ) -> tuple[str, dict]:
            persistent_fact_guards = [
                {
                    **issue,
                    "kind": f"fact:{str(issue.get('kind') or 'fidelity')}",
                }
                for issue in fidelity_issue_history.get(index, [])
            ]
            return rewrite_segment(
                index,
                style_issues=(
                    style_issue_history[index]
                    + persistent_fact_guards
                ),
                pass_number=active_style_pass,
            )

        try:
            with ThreadPoolExecutor(
                max_workers=min(worker_limit, len(repair_items)),
            ) as executor:
                repaired_values = list(executor.map(repair_style, repair_items))
        except RuntimeError as exc:
            print(
                "de-ai: optional detector structural repair skipped: " + str(exc),
                flush=True,
            )
            break
        for index, repaired in zip(repair_items, repaired_values, strict=True):
            chunk_texts[index] = repaired[0]
            result = repaired[1]

        candidate_fidelity = audit_fidelity(
            chunk_texts,
            focus_issues=all_fidelity_issue_history(),
        )
        remember_fidelity_issues(candidate_fidelity)
        fidelity_groups: dict[int, list[dict]] = {}
        for issue in candidate_fidelity.get("issues", []):
            index = int(issue.get("chunk") or 0) - 1
            if index in repair_indexes:
                fidelity_groups.setdefault(index, []).append(issue)
        optional_repair_failed = False
        for index, issues in fidelity_groups.items():
            accumulated_issues = fidelity_issue_history.get(index) or issues
            try:
                repaired, repair_meta = rewrite_segment(
                    index,
                    fidelity_issues=accumulated_issues,
                    style_issues=style_issue_history.get(index),
                    previous_candidate=chunk_texts[index],
                    pass_number=style_pass + 1,
                )
            except RuntimeError as exc:
                print(
                    "de-ai: optional detector fidelity repair skipped: " + str(exc),
                    flush=True,
                )
                optional_repair_failed = True
                break
            chunk_texts[index] = repaired
            result = repair_meta
        if optional_repair_failed:
            break
        if fidelity_groups:
            candidate_fidelity = audit_fidelity(
                chunk_texts,
                focus_issues=all_fidelity_issue_history(),
            )
            remember_fidelity_issues(candidate_fidelity)

        # A full-story audit can expose a pre-existing fact error in a chunk
        # that the structural pass did not touch.  If its local repair passes
        # on the structural branch, transplant only that audited correction
        # into the current best branch and re-audit the mixed candidate.  This
        # prevents style-score fallback from accidentally discarding a required
        # date, name, number, or ownership fix together with an optional style
        # experiment in another chunk.
        cross_branch_repairs = sorted(set(fidelity_groups) - set(repair_items))
        if (
            cross_branch_repairs
            and candidate_fidelity.get("valid")
            and candidate_fidelity.get("passed")
        ):
            mixed_best = list(best_chunk_texts)
            for index in cross_branch_repairs:
                mixed_best[index] = chunk_texts[index]
            mixed_fidelity = audit_fidelity(
                mixed_best,
                focus_issues=all_fidelity_issue_history(),
            )
            if mixed_fidelity.get("valid") and mixed_fidelity.get("passed"):
                mixed_style = audit_style(mixed_best)
                best_chunk_texts = mixed_best
                best_fidelity_audit = mixed_fidelity
                best_style_audit = mixed_style
                best_score = style_score(mixed_best, mixed_style)
        candidate_style = audit_style(chunk_texts)
        candidate_score = style_score(chunk_texts, candidate_style)
        if (
            candidate_fidelity.get("valid")
            and candidate_fidelity.get("passed")
            and candidate_score < best_score
        ):
            best_chunk_texts = list(chunk_texts)
            best_fidelity_audit = candidate_fidelity
            best_style_audit = candidate_style
            best_score = candidate_score
        fidelity_audit = candidate_fidelity
        style_audit = candidate_style

    chunk_texts = best_chunk_texts
    fidelity_audit = best_fidelity_audit
    style_audit = best_style_audit
    if style_audit.get("issues") or not style_audit.get("passed"):
        style_audit = {
            **style_audit,
            "exhausted": True,
            "selected_best": True,
            "repair_attempts": max(0, structural_repair_attempts),
        }
    candidate = "".join(chunk_texts)
    assessment = assess_de_ai_revision(
        source,
        candidate,
        min_length_ratio=0.88,
        require_substantial_revision=False,
    )
    candidate_visible = _visible_length(candidate)
    if (
        minimum_output_visible_characters
        and candidate_visible < minimum_output_visible_characters
    ):
        assessment["issues"].append({
            "code": "lineage_length_floor",
            "detail": (
                f"候选稿只有{candidate_visible}个可见字符，低于反馈链篇幅下限"
                f"{minimum_output_visible_characters}"
            ),
        })
        assessment["accepted"] = False
        assessment["minimum_visible_characters"] = minimum_output_visible_characters
    if not assessment["accepted"]:
        raise RuntimeError(
            "Detector-guided candidate failed deterministic validation: "
            + json.dumps(assessment.get("issues") or [], ensure_ascii=False)
        )
    result["content"] = candidate
    result["_chunk_lengths"] = [len(value) for value in chunk_texts]
    result["_story_ledger"] = "\n\n".join(
        f"【检测区段 {index + 1}】\n{ledgers[index]}"
        for index in repair_indexes
    )
    result["_fidelity_audit"] = fidelity_audit
    result["_style_audit"] = style_audit
    result["_detector_feedback"] = spans
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="warehouse")
    parser.add_argument("--generation-model", default="deepseek:deepseek-v4-flash")
    parser.add_argument("--revision-model", required=True)
    parser.add_argument(
        "--source-json",
        type=Path,
        help=(
            "Reuse the source field from a prior acceptance result instead of "
            "generating a new chapter."
        ),
    )
    parser.add_argument(
        "--source-stdin",
        action="store_true",
        help=(
            "Read the exact source chapter from standard input. This is useful "
            "for detector feedback on an in-memory/browser-verified candidate."
        ),
    )
    parser.add_argument(
        "--resume-last-round",
        action="store_true",
        help=(
            "With --source-json, continue from its last generated round instead "
            "of its original source."
        ),
    )
    parser.add_argument(
        "--reuse-story-ledger",
        action="store_true",
        help="Reuse the last stored ledger for prompt calibration on the same source text.",
    )
    parser.add_argument(
        "--direct-local-cli",
        action="store_true",
        help=(
            "Invoke the checked-out LocalCLIAdapter directly while keeping "
            "generation on the HTTP API."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Persist ledger, scene, and audit outputs so an interrupted CLI run can resume.",
    )
    parser.add_argument(
        "--story-ledger-file",
        type=Path,
        help="Reuse story_ledger from an acceptance artifact or checkpoint JSON.",
    )
    parser.add_argument(
        "--local-cli-timeout-seconds",
        type=int,
        default=900,
        help="Per-call timeout for direct local CLI acceptance calls.",
    )
    parser.add_argument(
        "--holistic-revision",
        action="store_true",
        help="Rewrite the ledger as one complete chapter instead of parallel scenes.",
    )
    parser.add_argument(
        "--detector-verdicts",
        help=(
            "Regenerate only externally rejected contiguous spans. Use comma-separated "
            "verdict:length pairs, for example warning:265,success:257,danger:532."
        ),
    )
    parser.add_argument(
        "--audit-model",
        help=(
            "Optional detector-guided ledger/audit model. The revision model still "
            "writes prose; defaults to the same model."
        ),
    )
    parser.add_argument(
        "--fidelity-repair-model",
        help=(
            "Optional detector-guided prose model used only after a fidelity audit "
            "finds story errors. Defaults to the revision model."
        ),
    )
    parser.add_argument(
        "--length-repair-model",
        help=(
            "Optional prose model used only for insertion-only feedback-chain "
            "length repair. Defaults to --fidelity-repair-model."
        ),
    )
    parser.add_argument(
        "--style-repair-model",
        help=(
            "Optional prose model used only for detector-guided structural "
            "repair. Defaults to the revision model."
        ),
    )
    parser.add_argument(
        "--structural-repair-attempts",
        type=int,
        choices=(0, 1, 2, 3),
        default=3,
        help="Bound detector-guided structural repair retries; production default is 3.",
    )
    parser.add_argument("--rounds", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.source_json and args.source_stdin:
        parser.error("--source-json and --source-stdin are mutually exclusive")
    if args.resume_last_round and not args.source_json:
        parser.error("--resume-last-round requires --source-json")

    reused_story_ledger = ""
    length_reference_visible_characters = 0
    if args.story_ledger_file:
        ledger_payload = json.loads(args.story_ledger_file.read_text(encoding="utf-8"))
        reused_story_ledger = str(ledger_payload.get("story_ledger") or "").strip()
        if not reused_story_ledger:
            ledger_rounds = (
                ledger_payload.get("rounds")
                if isinstance(ledger_payload.get("rounds"), list)
                else []
            )
            if ledger_rounds:
                reused_story_ledger = str(
                    ledger_rounds[-1].get("story_ledger") or ""
                ).strip()
        if not reused_story_ledger:
            raise RuntimeError("Story-ledger file does not contain a usable ledger")
    if args.source_stdin:
        source = _normalize_stdin_source(sys.stdin.read())
        authority_source = source
        generated = {"request_meta": {}}
        length_reference_visible_characters = _visible_length(source)
    elif args.source_json:
        prior = json.loads(args.source_json.read_text(encoding="utf-8"))
        prior_rounds = prior.get("rounds") if isinstance(prior.get("rounds"), list) else []
        prior_source = str(prior.get("source") or "").strip()
        authority_source = str(
            prior.get("authority_source") or prior_source
        ).strip()
        if args.resume_last_round and prior_rounds:
            source = str(prior_rounds[-1].get("text") or "").strip()
        else:
            source = prior_source
        generated = {
            "request_meta": prior.get("source_request_meta") or {},
        }
        prior_reference = int(prior.get("length_reference_visible_characters") or 0)
        if not prior_reference:
            prior_reference = _visible_length(str(prior.get("source") or source))
        length_reference_visible_characters = max(
            _visible_length(source),
            prior_reference,
        )
        if not reused_story_ledger and args.reuse_story_ledger and prior_rounds:
            reused_story_ledger = str(prior_rounds[-1].get("story_ledger") or "").strip()
    else:
        generated = _generate_source(args.base_url, args.generation_model, SCENARIOS[args.scenario])
        source = str(generated.get("content") or "").strip()
        authority_source = source
        length_reference_visible_characters = _visible_length(source)
    if not authority_source:
        raise RuntimeError("Acceptance source does not contain an immutable story authority")
    source_visible_characters = _visible_length(source)
    minimum_feedback_source_visible = round(
        length_reference_visible_characters * 0.85
    )
    if source_visible_characters < 1_500 and not (
        args.detector_verdicts
        and source_visible_characters >= minimum_feedback_source_visible
    ):
        raise RuntimeError(
            f"Generated chapter is unexpectedly short: {source_visible_characters}"
        )
    detector_spans = (
        _parse_detector_verdicts(args.detector_verdicts, source)
        if args.detector_verdicts
        else []
    )
    if detector_spans and args.rounds != 1:
        raise RuntimeError("--detector-verdicts requires --rounds 1")
    minimum_output_visible_characters = (
        round(length_reference_visible_characters * 0.95)
        if detector_spans
        else 0
    )

    result = {
        "scenario": args.scenario,
        "generation_model": args.generation_model,
        "revision_model": args.revision_model,
        "audit_model": args.audit_model or args.revision_model,
        "fidelity_repair_model": (
            args.fidelity_repair_model or args.revision_model
        ),
        "style_repair_model": args.style_repair_model or args.revision_model,
        "length_repair_model": (
            args.length_repair_model
            or args.fidelity_repair_model
            or args.revision_model
        ),
        "holistic_revision": args.holistic_revision,
        "detector_guided": bool(detector_spans),
        "length_reference_visible_characters": length_reference_visible_characters,
        "minimum_output_visible_characters": minimum_output_visible_characters,
        "source": source,
        "authority_source": authority_source,
        "authority_sha256": _content_hash(authority_source),
        "source_request_meta": generated.get("request_meta") or {},
        "rounds": [],
    }
    current = source
    active_story_ledger = reused_story_ledger
    checkpoint_root = args.checkpoint
    if checkpoint_root is None and args.output:
        checkpoint_root = Path(f"{args.output}.checkpoint.json")
    for round_number in range(1, args.rounds + 1):
        round_checkpoint = checkpoint_root
        if checkpoint_root is not None and args.rounds > 1:
            round_checkpoint = Path(f"{checkpoint_root}.round-{round_number}")
        if detector_spans:
            revised = _feedback_revise(
                args.base_url,
                args.revision_model,
                current,
                detector_spans,
                audit_model=args.audit_model,
                fidelity_repair_model=args.fidelity_repair_model,
                style_repair_model=args.style_repair_model,
                length_repair_model=args.length_repair_model,
                direct_local_cli=args.direct_local_cli,
                checkpoint_path=round_checkpoint,
                local_cli_timeout_seconds=max(60, args.local_cli_timeout_seconds),
                structural_repair_attempts=args.structural_repair_attempts,
                minimum_output_visible_characters=minimum_output_visible_characters,
            )
        else:
            revised = _revise(
                args.base_url,
                args.revision_model,
                current,
                authority_source=authority_source,
                direct_local_cli=args.direct_local_cli,
                story_ledger_override=active_story_ledger,
                checkpoint_path=round_checkpoint,
                local_cli_timeout_seconds=max(60, args.local_cli_timeout_seconds),
                holistic_revision=args.holistic_revision,
            )
        candidate = str(revised.get("content") or "").strip()
        if not active_story_ledger:
            active_story_ledger = str(revised.get("_story_ledger") or "").strip()
        assessment = _assess_lineage_round(
            authority_source,
            current,
            candidate,
            min_length_ratio=(0.88 if detector_spans else 0.9),
            require_substantial_revision=not bool(detector_spans),
        )
        candidate_visible_characters = _visible_length(candidate)
        if (
            minimum_output_visible_characters
            and candidate_visible_characters < minimum_output_visible_characters
        ):
            assessment["issues"].append({
                "code": "lineage_length_floor",
                "detail": (
                    f"候选稿只有{candidate_visible_characters}个可见字符，低于反馈链篇幅下限"
                    f"{minimum_output_visible_characters}"
                ),
            })
            assessment["accepted"] = False
            assessment["minimum_visible_characters"] = (
                minimum_output_visible_characters
            )
        fidelity_review = revised.get("_fidelity_audit") or {}
        style_review = revised.get("_style_audit") or {}
        for review_label, review in (
            ("故事保真审计", fidelity_review),
            ("表达结构审计", style_review),
        ):
            if review and not review.get("valid"):
                details = "；".join(
                    str(issue.get("detail") or "").strip()
                    for issue in review.get("issues", [])
                    if isinstance(issue, dict)
                    and str(issue.get("detail") or "").strip()
                )
                assessment["issues"].append({
                    "code": "review_unavailable",
                    "detail": f"{review_label}不可用：{details or '未返回有效结果'}",
                })
                assessment["accepted"] = False
        if fidelity_review.get("valid") and not fidelity_review.get("passed"):
            assessment["issues"].append({
                "code": "story_fidelity_rejected",
                "detail": "候选稿未通过故事保真审计，已保留正文与审计问题供检查。",
            })
            assessment["accepted"] = False
        result["rounds"].append({
            "round": round_number,
            "text": candidate,
            "story_ledger": revised.get("_story_ledger") or "",
            "chunk_lengths": revised.get("_chunk_lengths") or [],
            "fidelity_audit": revised.get("_fidelity_audit") or {},
            "style_audit": revised.get("_style_audit") or {},
            "detector_feedback": revised.get("_detector_feedback") or [],
            "assessment": assessment,
            "request_meta": revised.get("request_meta") or {},
        })
        if not assessment["accepted"]:
            break
        current = candidate

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
