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
    schedule_log_id: Optional[int] = Field(None, description="关联的定时发布执行记录ID（scheduler 传入）")
    schedule_id: Optional[int] = Field(None, description="关联的定时规则ID（scheduler 传入，用于补发）")


@router.post("/publish/batch", response_model=ApiResponse)
async def internal_publish_batch(
    req: InternalBatchPublishRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """内部批量发布（scheduler 定时发布任务调用，无需用户认证）

    与 /api/v1/product-publish/publish/batch 功能一致，
    但使用请求中传入的 user_id 代替认证用户的 ID。
    """
    from app.services.product_publish_service import ProductMaterialService, _material_to_dict
    from app.services.publish_batch_status_service import PublishBatchStatusService

    mat_svc = ProductMaterialService(session)
    materials = [
        _material_to_dict(m)
        for m in await mat_svc.list_by_ids(req.material_ids, req.user_id)
    ]

    if not materials:
        return ApiResponse(success=False, message="没有找到有效的素材")

    batch_id = str(uuid.uuid4())
    await PublishBatchStatusService.init_batch(
        batch_id=batch_id,
        account_ids=req.account_ids,
        material_count=len(materials),
    )

    # 创建后台任务（与 publish_batch 使用相同的后台发布逻辑）
    from app.api.routes.product_publish import _run_batch_publish_background
    background_tasks.add_task(
        _run_batch_publish_background,
        user_id=req.user_id,
        account_ids=req.account_ids,
        materials=materials,
        batch_id=batch_id,
        schedule_log_id=req.schedule_log_id,
        schedule_id=req.schedule_id,
    )

    return ApiResponse(
        success=True,
        message=f"批量发布任务已提交，共 {len(req.account_ids)} 个账号 × {len(materials)} 件商品",
        data={
            "batch_id": batch_id,
            "total": len(req.account_ids) * len(materials),
        },
    )
