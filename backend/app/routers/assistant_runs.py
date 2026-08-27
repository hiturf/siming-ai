"""Small lifecycle commands for durable project-assistant runs."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.assistant.interfaces.workspace_dependencies import assistant_workspace
from ..modules.operations.interfaces.dependencies import get_operation_service

router = APIRouter(tags=["ai-writer"])


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/cancel")
async def cancel_assistant_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Cancel one durable assistant run after verifying project ownership."""
    run = assistant_workspace(db).run(project_id, run_id)
    if not run:
        raise NotFoundError("助手任务不存在")
    if not run.operation_id:
        raise ValidationError("助手任务尚未登记可取消的 Operation")
    status, operation = await get_operation_service().action(run.operation_id, "cancel")
    if status == "not_found":
        raise NotFoundError("助手任务对应的 Operation 不存在")
    if status != "ok":
        raise ValidationError("助手任务当前不支持取消")
    return ApiResponse.success(
        data={
            "run_id": run.id,
            "operation_id": run.operation_id,
            "operation": operation,
        },
        message="助手任务取消请求已确认",
    )
