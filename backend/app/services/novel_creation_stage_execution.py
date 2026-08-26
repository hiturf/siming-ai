"""Orchestration for one resumable novel-creation stage run."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.database.models import NovelCreationSession, NovelCreationStageRun
from app.services.context_orchestrator import ContextOrchestrator
from app.services.novel_creation_authoring import (
    _validate_stage,
)
from app.services.novel_creation_entities import (
    ENTITY_TYPES_BY_ARTIFACT,
    _extract_records,
    get_creation_entity,
)
from app.services.novel_creation_stage_runtime import generate_stage_data, stage_tool_result
from app.services.novel_creation_workspace import (
    STAGE_LABELS,
    STAGE_ORDER,
    add_run_event,
    complete_run,
    create_run,
    derive_stage,
    fail_run,
    patch_session,
    save_compact_concepts,
    save_stage,
    serialize_run,
    serialize_session,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _session(db: Session, session_id: str) -> NovelCreationSession | None:
    return db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()


@dataclass
class StageExecution:
    db: Session
    project_id: str
    args: dict[str, Any]
    session_id: str
    session: NovelCreationSession
    stage: str
    model: str
    use_model: bool
    auto_confirm: bool
    operation: str
    working_draft: dict[str, Any]
    run: NovelCreationStageRun
    orchestrator: ContextOrchestrator
    manifest: Any
    ensure_not_cancelled: Any
    generate_concepts: Any
    normalize_stage: Any
    enhance_with_model: Any
    expected_revision: int
    entity_target: dict[str, Any] | None = None
    active_stage: str = ""
    generated: dict[str, Any] = field(default_factory=dict)
    run_metadata: list[dict[str, Any]] = field(default_factory=list)


class StageRevisionConflict(ValueError):
    """A user edit won the race with a long-running stage generation."""

    failure_class = "revision_conflict"


def _preserve_conflict_candidate(
    exc: StageRevisionConflict,
    *,
    artifact: str,
    data: dict[str, Any],
) -> None:
    """Attach a generated candidate to the conflict without writing the artifact."""
    exc.candidate_artifact = artifact
    exc.candidate_data = deepcopy(data)


def _capture_model_diagnostic(
    context: StageExecution,
    stage: str,
    metadata: dict[str, Any],
) -> None:
    """Persist the complete raw model reply while keeping public events bounded."""
    raw = metadata.pop("_diagnostic_raw", None)
    if not isinstance(raw, str) or not raw:
        return
    diagnostics = list(context.run.diagnostics_json or [])
    diagnostics.append({
        "stage": stage,
        "repair_method": metadata.get("repair_method"),
        "warning": metadata.get("warning"),
        "raw_response": raw,
        "captured_at": datetime.utcnow().isoformat(),
    })
    context.run.diagnostics_json = diagnostics


def _revision_conflict(expected: int, actual: int) -> StageRevisionConflict:
    return StageRevisionConflict(
        "立项草稿版本已经变化，本次生成结果未保存，以免覆盖你的人工修改。"
        f"任务基于版本 {expected}，当前版本为 {actual}；请确认当前内容后重新生成本阶段。"
    )


def _save_with_revision_cas(context: StageExecution, saver: Any) -> Any:
    """Apply one stage save only when its frozen input revision still owns the draft."""
    context.ensure_not_cancelled(context.db, context.run)
    context.db.refresh(context.session)
    actual_revision = int(context.session.revision or 0)
    if actual_revision != context.expected_revision:
        raise _revision_conflict(context.expected_revision, actual_revision)

    with context.db.no_autoflush:
        saved = deepcopy(saver())
        next_revision = int(context.session.revision or 0)
        values = {
            "draft_json": deepcopy(context.session.draft_json),
            "checkpoints_json": deepcopy(context.session.checkpoints_json),
            "last_error_json": deepcopy(context.session.last_error_json),
            "current_stage": context.session.current_stage,
            "status": context.session.status,
            "revision": next_revision,
        }
        result = context.db.execute(
            update(NovelCreationSession)
            .where(
                NovelCreationSession.id == context.session.id,
                NovelCreationSession.revision == context.expected_revision,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
    if result.rowcount != 1:
        context.db.expire(context.session)
        current = _session(context.db, context.session_id)
        raise _revision_conflict(
            context.expected_revision,
            int(getattr(current, "revision", context.expected_revision) or 0),
        )
    context.db.expire(context.session)
    context.db.refresh(context.session)
    context.expected_revision = next_revision
    return saved


def _prepare_execution(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    *,
    ensure_not_cancelled: Any,
    generate_concepts: Any,
    normalize_stage: Any,
    enhance_with_model: Any,
) -> tuple[StageExecution | None, dict[str, Any] | None]:
    session_id = _text(args.get("session_id"))
    stage = _text(args.get("stage"))
    session = _session(db, session_id)
    if not session:
        return None, {
            "tool": "generate_creation_artifact",
            "status": "skipped",
            "detail": "Session not found",
            "data": None,
        }
    if stage not in {*STAGE_ORDER, "all"}:
        return None, {
            "tool": "generate_creation_artifact",
            "status": "skipped",
            "detail": "Unknown stage",
            "data": None,
        }
    if isinstance(args.get("session_patch"), dict):
        patch_session(session, args["session_patch"])
    model = _text(args.get("model"))
    operation = _text(args.get("operation")) or "generate"
    existing_run_id = _text(args.get("_run_id"))
    run = (
        db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == existing_run_id).first()
        if existing_run_id
        else None
    )
    run_request = run.request_json if run and isinstance(run.request_json, dict) else {}
    current_draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    is_resume = bool(args.get("_resume") or run_request.get("_resume"))
    snapshot = run_request.get("input_snapshot")
    working_draft = deepcopy(current_draft) if is_resume else (
        deepcopy(snapshot) if isinstance(snapshot, dict) else deepcopy(current_draft)
    )
    instruction = _text(args.get("instruction"))
    if instruction:
        working_draft["_refinement_instruction"] = instruction
    entity_target: dict[str, Any] | None = None
    entity_id = _text(args.get("entity_id"))
    entity_type = _text(args.get("entity_type"))
    if entity_id:
        entity = get_creation_entity(db, entity_id)
        if not entity or entity.session_id != session.id or entity.status == "deleted":
            raise ValueError("目标实体不存在或已删除")
        if entity.artifact_key != stage:
            raise ValueError("目标实体不属于当前立项对象")
        entity_target = {
            "id": entity.id,
            "entity_type": entity.entity_type,
            "entity_key": entity.entity_key,
            "mode": "existing",
        }
    elif entity_type:
        if entity_type not in ENTITY_TYPES_BY_ARTIFACT.get(stage, frozenset()):
            raise ValueError("目标实体类型不属于当前立项对象")
        entity_target = {
            "entity_type": entity_type,
            "mode": "new",
            "count": (
                max(1, min(int(args["entity_count"]), 20))
                if args.get("entity_count")
                else None
            ),
        }
    if entity_target:
        working_draft["_entity_target"] = deepcopy(entity_target)
    orchestrator = ContextOrchestrator(db)
    manifest_id = _text(args.get("context_manifest_id")) or _text(
        getattr(run, "context_manifest_id", "")
    )
    manifest = orchestrator.get_manifest(manifest_id) if manifest_id else None
    if manifest is None:
        interview = working_draft.get("interview")
        manifest = orchestrator.prepare(
            project_id=None,
            task_type="new_project",
            model=model or None,
            execution_route="novel_creation",
            session_id=session.id,
            arguments={
                "session_id": session.id,
                "session": {"brief": session.user_brief, "draft": working_draft},
                "answers": interview.get("history") if isinstance(interview, dict) else [],
                "confirmed_stages": working_draft.get("stages") or {},
                "author_constraints": session.user_brief or "",
                "stage": stage,
            },
        )
    governed_args = {**args, "context_manifest_id": manifest.id}
    if run is None:
        run = create_run(db, session, stage, governed_args)
        commit_session(db)
    elif not run.context_manifest_id:
        run.context_manifest_id = manifest.id
        commit_session(db)
    return StageExecution(
        db=db,
        project_id=project_id,
        args=args,
        session_id=session_id,
        session=session,
        stage=stage,
        model=model,
        use_model=bool(args.get("use_model", bool(model))),
        auto_confirm=bool(args.get("auto_confirm", stage == "all")),
        operation=operation,
        working_draft=working_draft,
        run=run,
        orchestrator=orchestrator,
        manifest=manifest,
        ensure_not_cancelled=ensure_not_cancelled,
        generate_concepts=generate_concepts,
        normalize_stage=normalize_stage,
        enhance_with_model=enhance_with_model,
        expected_revision=int(
            session.revision or 0
            if is_resume
            else (run.input_revision if run.input_revision is not None else session.revision or 0)
        ),
        entity_target=entity_target,
        active_stage=stage,
    ), None


def _merge_entity_generation(
    context: StageExecution,
    stage: str,
    baseline: dict[str, Any],
    generated: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Keep unrelated rows byte-for-byte stable during entity-level generation."""
    target = context.entity_target
    if not target:
        return generated, None
    baseline_records = _extract_records(stage, baseline)
    generated_records = _extract_records(stage, generated)
    entity_type = target["entity_type"]
    candidates = [item for item in generated_records if item["entity_type"] == entity_type]
    if not candidates:
        raise ValueError(f"模型没有返回可用的 {entity_type} 实体")

    merged = deepcopy(baseline)
    if target["mode"] == "existing":
        current = next(
            (
                item for item in baseline_records
                if item["entity_type"] == entity_type and item["entity_key"] == target["entity_key"]
            ),
            None,
        )
        if not current:
            raise ValueError("目标实体已不在当前立项数据中")
        replacement = next(
            (item for item in candidates if item["entity_key"] == target["entity_key"]),
            candidates[0],
        )
        replacement_data = deepcopy(replacement["data"])
        locks = context.working_draft.get("artifact_locks")
        locked_paths = (
            locks.get(stage)
            if isinstance(locks, dict) and isinstance(locks.get(stage), list)
            else []
        )
        entity_prefix = f"/{current['field']}/{current['index']}"
        for locked_path in locked_paths:
            if locked_path == entity_prefix:
                replacement_data = deepcopy(current["data"])
                break
            if not str(locked_path).startswith(entity_prefix + "/"):
                continue
            parts = [
                part.replace("~1", "/").replace("~0", "~")
                for part in str(locked_path)[len(entity_prefix) + 1:].split("/")
            ]
            source: Any = current["data"]
            destination: Any = replacement_data
            try:
                for part in parts[:-1]:
                    source = source[int(part)] if isinstance(source, list) else source[part]
                    destination = (
                        destination[int(part)]
                        if isinstance(destination, list)
                        else destination[part]
                    )
                leaf = parts[-1]
                value = source[int(leaf)] if isinstance(source, list) else source[leaf]
                if isinstance(destination, list):
                    destination[int(leaf)] = deepcopy(value)
                else:
                    destination[leaf] = deepcopy(value)
            except (KeyError, IndexError, TypeError, ValueError):
                # A missing generated container cannot safely carry the lock;
                # retain the complete entity instead of weakening author intent.
                replacement_data = deepcopy(current["data"])
                break
        merged[current["field"]][current["index"]] = replacement_data
        summary = {
            "mode": context.operation,
            "entity_id": target["id"],
            "entity_type": entity_type,
            "entity_key": target["entity_key"],
            "preserved_entity_count": max(0, len(baseline_records) - 1),
        }
    else:
        existing_keys = {
            (item["entity_type"], item["entity_key"]) for item in baseline_records
        }
        additions = [
            item for item in candidates
            if (item["entity_type"], item["entity_key"]) not in existing_keys
        ][: int(target.get("count") or 20)]
        if not additions:
            raise ValueError(f"模型没有生成新的 {entity_type} 对象；现有数据保持不变")
        for addition in additions:
            collection = addition["field"]
            merged.setdefault(collection, [])
            if not isinstance(merged[collection], list):
                raise ValueError("目标实体集合格式不正确")
            merged[collection].append(deepcopy(addition["data"]))
        summary = {
            "mode": "generate",
            "entity_type": entity_type,
            "entity_keys": [item["entity_key"] for item in additions],
            "created_entity_count": len(additions),
            "preserved_entity_count": len(baseline_records),
        }
    return merged, summary


async def _generate_concept_stage(context: StageExecution) -> None:
    draft = context.working_draft
    author_led = draft.get("creation_mode") == "author_led"
    concept_label = "作者方案" if author_led else "创意方向"
    action = "按要求调整" if context.operation == "refine" else "生成"
    add_run_event(
        context.db,
        context.run,
        "stage_progress",
        "running",
        f"正在{action}{concept_label}",
        {
            "stage": "concepts",
            "model_source": context.model or "none",
            "storage_target": "session_draft",
        },
    )
    context.run.current_message = f"正在{action}{concept_label}"
    commit_session(context.db)
    if not context.use_model or not context.model:
        raise RuntimeError("当前没有可用于立项生成的模型")
    context.ensure_not_cancelled(context.db, context.run)
    concepts, metadata = await context.generate_concepts(
        context.session,
        context.model,
        context_manifest=context.manifest,
        input_snapshot=draft,
    )
    context.ensure_not_cancelled(context.db, context.run)
    source = "model" if metadata.get("result_mode") == "model" else "model_repaired"
    _capture_model_diagnostic(context, "concepts", metadata)
    context.run_metadata.append(metadata)
    try:
        concept_stage = _save_with_revision_cas(
            context,
            lambda: save_compact_concepts(context.session, concepts, source=source),
        )
    except StageRevisionConflict as exc:
        _preserve_conflict_candidate(
            exc,
            artifact="concepts",
            data={"options": deepcopy(concepts), "selected_concept_id": None},
        )
        raise
    context.generated["concepts"] = deepcopy(concept_stage.get("data") or {})
    add_run_event(
        context.db,
        context.run,
        "stage_completed",
        "ok",
        f"{concept_label}已保存",
        {"stage": "concepts", **metadata, "storage_target": "session_draft"},
    )
    commit_session(context.db)


async def _generate_regular_stages(context: StageExecution) -> None:
    stages = (
        [name for name in STAGE_ORDER if name not in {"constraints", "concepts"}]
        if context.stage == "all"
        else [context.stage]
    )
    completed_stages = {
        str((event.payload_json or {}).get("stage"))
        for event in (context.run.events or [])
        if event.event_type == "stage_completed" and isinstance(event.payload_json, dict)
    } if context.args.get("_resume") else set()
    for name in stages:
        if name in completed_stages:
            existing = ((context.working_draft.get("stages") or {}).get(name) or {}).get("data")
            if isinstance(existing, dict):
                context.generated[name] = deepcopy(existing)
            continue
        context.active_stage = name
        context.ensure_not_cancelled(context.db, context.run)
        label = STAGE_LABELS.get(name, name)
        add_run_event(
            context.db,
            context.run,
            "stage_progress",
            "running",
            f"正在生成{label}",
            {
                "stage": name,
                "model_source": context.model or "contract",
                "storage_target": "session_draft",
            },
        )
        context.run.current_message = f"正在生成{label}"
        commit_session(context.db)
        existing_stage = ((context.working_draft.get("stages") or {}).get(name) or {})
        baseline = (
            deepcopy(existing_stage.get("data"))
            if (context.operation == "refine" or context.entity_target)
            and isinstance(existing_stage.get("data"), dict)
            else derive_stage(context.session, name, context.working_draft)
        )
        data, source, metadata = await generate_stage_data(
            context.session,
            stage=name,
            baseline=baseline,
            model=context.model,
            use_model=context.use_model,
            manifest=context.manifest,
            working_draft=context.working_draft,
            enhance=context.enhance_with_model,
        )
        context.ensure_not_cancelled(context.db, context.run)
        _capture_model_diagnostic(context, name, metadata)
        context.run_metadata.append(metadata)
        data = context.normalize_stage(name, data, baseline)
        data, entity_summary = _merge_entity_generation(context, name, baseline, data)
        _validate_stage(name, data)
        def save_generated_stage(
            stage_name: str = name,
            stage_data: dict[str, Any] = data,
            stage_source: str = source,
            summary: dict[str, Any] | None = entity_summary,
        ) -> dict[str, Any]:
            return save_stage(
                context.session,
                stage_name,
                stage_data,
                confirm=context.auto_confirm,
                source=stage_source,
                change_type=(f"entity_{context.operation}" if summary else context.operation),
                change_summary=([summary] if summary else None),
                run_id=context.run.id,
                operation_id=context.run.operation_id,
            )

        try:
            _save_with_revision_cas(context, save_generated_stage)
        except StageRevisionConflict as exc:
            _preserve_conflict_candidate(exc, artifact=name, data=data)
            raise
        context.working_draft.setdefault("stages", {})[name] = {
            "status": "confirmed" if context.auto_confirm else "generated",
            "data": deepcopy(data),
            "source": source,
        }
        context.generated[name] = deepcopy(data)
        if entity_summary:
            context.generated["entity_change"] = entity_summary
        add_run_event(
            context.db,
            context.run,
            "stage_completed",
            "ok",
            f"{label}已保存",
            {"stage": name, **metadata, "storage_target": "session_draft"},
        )
        commit_session(context.db)


def _finish_execution(context: StageExecution) -> dict[str, Any]:
    warnings = [str(item.get("warning")) for item in context.run_metadata if item.get("warning")]
    modes = {item.get("result_mode") for item in context.run_metadata}
    result_mode = (
        "repaired" if "repaired" in modes
        else "model"
    )
    context.ensure_not_cancelled(context.db, context.run)
    complete_run(context.db, context.run, {
        "stages": context.generated,
        "attempt": max([int(item.get("attempt") or 0) for item in context.run_metadata] or [0]),
        "result_mode": result_mode,
        "warning": "；".join(dict.fromkeys(warnings)) or None,
    })
    context.orchestrator.mark_consumed(context.manifest)
    commit_session(context.db)
    context.db.refresh(context.run)
    return stage_tool_result("ok", "Novel creation stage generated", context.run, context.session)


async def execute_creation_artifact_generation(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    *,
    ensure_not_cancelled: Any,
    generate_concepts: Any,
    normalize_stage: Any,
    enhance_with_model: Any,
) -> dict[str, Any]:
    try:
        context, early_result = _prepare_execution(
            db,
            project_id,
            args,
            ensure_not_cancelled=ensure_not_cancelled,
            generate_concepts=generate_concepts,
            normalize_stage=normalize_stage,
            enhance_with_model=enhance_with_model,
        )
    except Exception as exc:
        db.rollback()
        return {
            "tool": "generate_creation_artifact",
            "status": "error",
            "detail": str(exc),
            "data": None,
        }
    if early_result is not None:
        return early_result
    assert context is not None
    try:
        context.ensure_not_cancelled(db, context.run)
        if context.stage == "concepts":
            await _generate_concept_stage(context)
        else:
            await _generate_regular_stages(context)
        return _finish_execution(context)
    except Exception as exc:
        db.rollback()
        session = _session(db, context.session_id)
        run = (
            db.query(NovelCreationStageRun)
            .filter(NovelCreationStageRun.id == context.run.id)
            .first()
        )
        if run and session:
            fail_run(db, run, exc, failed_stage=context.active_stage)
            commit_session(db)
            return stage_tool_result("error", str(exc), run, session)
        return {
            "tool": "generate_creation_artifact",
            "status": "error",
            "detail": str(exc),
            "data": None,
        }
