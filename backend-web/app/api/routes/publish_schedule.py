"""
定时发布 API 路由

功能：
1. 定时发布规则 CRUD
2. 执行历史查询
3. 手动触发
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from app.services.publish_schedule_service import PublishScheduleService
from common.models.user import User, UserRole
from common.schemas.common import ApiResponse

router = APIRouter(prefix="/product-publish/schedules", tags=["商品发布-定时发布"])


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


# ==================== 请求模型 ====================

class ScheduleConfig(BaseModel):
    """调度时间配置"""
    datetime: Optional[str] = Field(None, description="单次执行时间 ISO 格式")
    times: Optional[List[str]] = Field(None, description="时间点列表，如 ['08:00', '20:00']")
    days: Optional[List[int]] = Field(None, description="星期几，1=周一，7=周日")
    time_range: Optional[Dict[str, str]] = Field(None, description="时间段，如 {start:'18:00', end:'22:00'}")
    random: bool = Field(False, description="是否在时间段/时间点内随机")


class CreateScheduleRequest(BaseModel):
    """创建定时规则请求"""
    name: str = Field(..., min_length=1, max_length=100, description="规则名称")
    schedule_mode: str = Field("daily", description="重复模式：once/daily/weekly")
    schedule_config: Dict = Field(..., description="时间配置")
    account_ids: List[str] = Field(..., min_length=1, description="闲鱼账号ID列表")
    material_ids: List[int] = Field(..., min_length=1, description="素材ID列表")


class UpdateScheduleRequest(BaseModel):
    """更新定时规则请求（所有字段可选）"""
    name: Optional[str] = Field(None, max_length=100)
    schedule_mode: Optional[str] = None
    schedule_config: Optional[Dict] = None
    account_ids: Optional[List[str]] = None
    material_ids: Optional[List[int]] = None
    enabled: Optional[bool] = None


# ==================== 规则 CRUD ====================

@router.post("", response_model=ApiResponse)
async def create_schedule(
    req: CreateScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """创建定时发布规则"""
    svc = PublishScheduleService(session)
    try:
        schedule = await svc.create(current_user.id, {
            "name": req.name,
            "schedule_mode": req.schedule_mode,
            "schedule_config": req.schedule_config,
            "account_ids": req.account_ids,
            "material_ids": req.material_ids,
        })
        from app.services.publish_schedule_service import _schedule_to_dict
        return ApiResponse(success=True, message="定时规则创建成功", data=_schedule_to_dict(schedule))
    except Exception as e:
        await session.rollback()
        return ApiResponse(success=False, message=f"创建失败: {str(e)}")


@router.get("", response_model=ApiResponse)
async def list_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询定时规则列表"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_schedules(user_id=query_user_id, page=page, page_size=page_size)
    # 补充账号数/素材数等摘要信息
    for item in data["list"]:
        item["account_count"] = len(item.get("account_ids", []))
        item["material_count"] = len(item.get("material_ids", []))
    return ApiResponse(success=True, message="查询成功", data=data)


@router.get("/{schedule_id}", response_model=ApiResponse)
async def get_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询单条规则详情"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在")
    from app.services.publish_schedule_service import _schedule_to_dict
    return ApiResponse(success=True, message="查询成功", data=_schedule_to_dict(schedule))


@router.put("/{schedule_id}", response_model=ApiResponse)
async def update_schedule(
    schedule_id: int,
    req: UpdateScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """更新定时规则"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    schedule = await svc.update(schedule_id, query_user_id, update_data)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    from app.services.publish_schedule_service import _schedule_to_dict
    return ApiResponse(success=True, message="规则更新成功", data=_schedule_to_dict(schedule))


@router.delete("/{schedule_id}", response_model=ApiResponse)
async def delete_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """删除定时规则（同时取消关联的 pending 执行记录）"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    ok = await svc.delete(schedule_id, query_user_id)
    if not ok:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    return ApiResponse(success=True, message="规则已删除")


@router.patch("/{schedule_id}/toggle", response_model=ApiResponse)
async def toggle_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """切换规则启用/禁用状态"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.toggle(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    from app.services.publish_schedule_service import _schedule_to_dict
    return ApiResponse(
        success=True,
        message=f"规则已{'启用' if schedule.enabled else '禁用'}",
        data=_schedule_to_dict(schedule),
    )


@router.post("/{schedule_id}/trigger", response_model=ApiResponse)
async def trigger_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """手动立即触发一次定时规则（不影响原调度周期）"""
    import uuid

    from loguru import logger

    from app.services.product_publish_service import ProductMaterialService, _material_to_dict
    from app.services.publish_batch_status_service import PublishBatchStatusService

    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")

    # 加载素材
    mat_svc = ProductMaterialService(session)
    materials = [
        _material_to_dict(m)
        for m in await mat_svc.list_by_ids(schedule.material_ids, current_user.id)
    ]
    if not materials:
        return ApiResponse(success=False, message="没有找到有效的素材")

    account_ids = schedule.account_ids
    batch_id = str(uuid.uuid4())

    # 创建执行记录
    from common.utils.time_utils import get_beijing_now_naive
    total_count = len(account_ids) * len(materials)
    log_entry = await svc.create_log(schedule_id, get_beijing_now_naive(), total_count)
    await svc.update_log(log_entry.id, {"batch_id": batch_id, "status": "running"})

    # 初始化 batch 状态
    await PublishBatchStatusService.init_batch(
        batch_id=batch_id, account_ids=account_ids, material_count=len(materials),
    )

    # 通过 BackgroundTasks 异步执行
    # 注意：由于这是在路由中使用，需要手动构建后台任务
    from app.api.routes.product_publish import _run_batch_publish_background
    import asyncio

    asyncio.create_task(
        _run_batch_publish_background(
            user_id=current_user.id,
            account_ids=account_ids,
            materials=materials,
            batch_id=batch_id,
        )
    )

    logger.info(
        f"[定时发布] 手动触发 schedule_id={schedule_id}, batch_id={batch_id}, "
        f"{len(account_ids)} 账号 × {len(materials)} 素材"
    )

    return ApiResponse(
        success=True,
        message=f"已触发批量发布，{len(account_ids)} 账号 × {len(materials)} 素材",
        data={"batch_id": batch_id, "log_id": log_entry.id},
    )


# ==================== 执行日志 ====================

@router.get("/{schedule_id}/logs", response_model=ApiResponse)
async def list_schedule_logs(
    schedule_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询某规则的历史执行记录"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    # 先验证规则属于当前用户
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权查看")
    data = await svc.list_logs(schedule_id=schedule_id, page=page, page_size=page_size)
    return ApiResponse(success=True, message="查询成功", data=data)


@router.get("/logs/global", response_model=ApiResponse)
async def list_all_schedule_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询所有定时规则的执行历史（全局视图）"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_logs(schedule_id=None, page=page, page_size=page_size, user_id=query_user_id)
    return ApiResponse(success=True, message="查询成功", data=data)
