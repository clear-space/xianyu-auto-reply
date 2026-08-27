"""
内部服务间 API（无需认证，仅供 scheduler 等内部服务调用）

功能：
1. 批量发布（scheduler 定时发布任务调用）
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from common.schemas.common import ApiResponse

router = APIRouter(prefix="/internal", tags=["内部服务"])


class InternalBatchPublishRequest(BaseModel):
    """内部批量发布请求（含 user_id，无需认证）"""
    user_id: int = Field(..., description="所属用户ID")
    account_ids: List[str] = Field(..., min_length=1, description="账号ID列表")
    material_ids: List[int] = Field(..., min_length=1, description="素材ID列表")
    schedule_id: Optional[int] = Field(None, description="定时发布规则ID（scheduler 传入）")
    schedule_log_id: Optional[int] = Field(None, description="关联的定时发布执行记录ID（scheduler 传入）")
    batch_id: Optional[str] = Field(None, description="批次ID（scheduler 预生成，保证执行记录与发布日志同批次）")


@router.post("/publish/batch", response_model=ApiResponse)
async def internal_publish_batch(
    req: InternalBatchPublishRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """内部批量发布（scheduler 定时发布任务调用，无需用户认证）

    传入 schedule_id 时走定时发布共享执行器（随机选料/去重/自动补发）；
    否则退化为普通批量发布（兼容旧调用方）。
    """
    from sqlalchemy import select

    from app.services.publish_batch_status_service import PublishBatchStatusService
    from common.models.publish_schedule import PublishSchedule
    from common.models.publish_schedule_log import PublishScheduleLog

    schedule = None
    if req.schedule_id:
        schedule = (
            await session.execute(
                select(PublishSchedule).where(PublishSchedule.id == req.schedule_id)
            )
        ).scalar_one_or_none()
    elif req.schedule_log_id:
        # 兼容：未传 schedule_id 时从执行记录反查规则
        log_entry = (
            await session.execute(
                select(PublishScheduleLog).where(PublishScheduleLog.id == req.schedule_log_id)
            )
        ).scalar_one_or_none()
        if log_entry:
            schedule = (
                await session.execute(
                    select(PublishSchedule).where(PublishSchedule.id == log_entry.schedule_id)
                )
            ).scalar_one_or_none()

    batch_id = req.batch_id or str(uuid.uuid4())

    if schedule:
        # 定时发布共享执行器（手动触发与 scheduler 共用的唯一实现）
        from app.services.scheduled_publish_executor import ScheduledPublishExecutor

        await PublishBatchStatusService.init_batch(
            batch_id=batch_id,
            account_ids=list(schedule.account_ids or []),
            material_count=len(schedule.material_ids or []),
        )
        schedule_data = {
            "id": schedule.id,
            "account_ids": list(schedule.account_ids or []),
            "material_ids": list(schedule.material_ids or []),
            "publish_mode": schedule.publish_mode or "specified",
            "random_count": schedule.random_count,
            "deduplicate_enabled": bool(schedule.deduplicate_enabled),
            "weight_algorithm_id": schedule.weight_algorithm_id,
        }
        background_tasks.add_task(
            ScheduledPublishExecutor.run,
            user_id=req.user_id,
            schedule_data=schedule_data,
            batch_id=batch_id,
            schedule_log_id=req.schedule_log_id,
        )
        return ApiResponse(
            success=True,
            message=f"定时发布任务已提交（{schedule_data['publish_mode']} 模式）",
            data={"batch_id": batch_id},
        )

    # 兜底：普通批量发布（与 /api/v1/product-publish/publish/batch 一致）
    from app.services.product_publish_service import ProductMaterialService, _material_to_dict
    from app.api.routes.product_publish import _run_batch_publish_background

    mat_svc = ProductMaterialService(session)
    materials = [
        _material_to_dict(m)
        for m in await mat_svc.list_by_ids(req.material_ids, req.user_id)
    ]

    if not materials:
        return ApiResponse(success=False, message="没有找到有效的素材")

    await PublishBatchStatusService.init_batch(
        batch_id=batch_id,
        account_ids=req.account_ids,
        material_count=len(materials),
    )

    background_tasks.add_task(
        _run_batch_publish_background,
        user_id=req.user_id,
        account_ids=req.account_ids,
        materials=materials,
        batch_id=batch_id,
        schedule_log_id=req.schedule_log_id,
    )

    return ApiResponse(
        success=True,
        message=f"批量发布任务已提交，共 {len(req.account_ids)} 个账号 × {len(materials)} 件商品",
        data={
            "batch_id": batch_id,
            "total": len(req.account_ids) * len(materials),
        },
    )


class InternalOfflineExecuteRequest(BaseModel):
    """内部自动下架请求（含 user_id，无需认证）"""
    user_id: int = Field(..., description="所属用户ID")
    schedule_id: Optional[int] = Field(None, description="下架规则ID（scheduler 传入）")
    schedule_log_id: Optional[int] = Field(None, description="关联的下架执行记录ID（scheduler 传入）")
    batch_id: Optional[str] = Field(None, description="批次ID（scheduler 预生成）")


@router.post("/offline/execute", response_model=ApiResponse)
async def internal_offline_execute(
    req: InternalOfflineExecuteRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """内部自动下架（scheduler 定时下架任务调用，无需用户认证）

    走自动下架共享执行器（筛选/分组下架/删本地），与手动触发共用同一份实现。
    """
    from sqlalchemy import select

    from app.services.offline_executor import OfflineExecutor
    from common.models.offline_schedule import OfflineSchedule
    from common.models.offline_schedule_log import OfflineScheduleLog

    schedule = None
    if req.schedule_id:
        schedule = (
            await session.execute(
                select(OfflineSchedule).where(OfflineSchedule.id == req.schedule_id)
            )
        ).scalar_one_or_none()
    elif req.schedule_log_id:
        log_entry = (
            await session.execute(
                select(OfflineScheduleLog).where(OfflineScheduleLog.id == req.schedule_log_id)
            )
        ).scalar_one_or_none()
        if log_entry:
            schedule = (
                await session.execute(
                    select(OfflineSchedule).where(OfflineSchedule.id == log_entry.schedule_id)
                )
            ).scalar_one_or_none()

    if not schedule:
        return ApiResponse(success=False, message="下架规则不存在")

    batch_id = req.batch_id or str(uuid.uuid4())
    schedule_data = {
        "id": schedule.id,
        "account_ids": list(schedule.account_ids or []),
        "max_count": schedule.max_count,
        "delist_algorithm_id": schedule.delist_algorithm_id,
    }
    background_tasks.add_task(
        OfflineExecutor.run,
        user_id=req.user_id,
        schedule_data=schedule_data,
        batch_id=batch_id,
        schedule_log_id=req.schedule_log_id,
    )
    return ApiResponse(
        success=True,
        message=f"自动下架任务已提交（{len(schedule_data['account_ids'])} 个账号）",
        data={"batch_id": batch_id},
    )
