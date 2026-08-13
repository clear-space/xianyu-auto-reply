"""
自动下架规则 API 路由
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from app.services.offline_schedule_service import OfflineScheduleService
from common.models.user import User, UserRole
from common.schemas.common import ApiResponse

router = APIRouter(prefix="/product-publish/offline-schedules", tags=["商品发布-自动下架"])


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


class CreateOfflineScheduleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="规则名称")
    age_days: int = Field(7, ge=1, description="已上架天数阈值 X")
    no_order_days: int = Field(7, ge=1, description="最近N天无订单 Y")
    offline_count: int = Field(5, ge=1, description="下架数量上限 Z")
    schedule_mode: str = Field("daily", description="daily / weekly")
    schedule_config: Dict = Field(default={}, description="时间配置JSON")
    account_ids: List[str] = Field(..., min_length=1, description="闲鱼账号ID列表")


class UpdateOfflineScheduleRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    age_days: Optional[int] = None
    no_order_days: Optional[int] = None
    offline_count: Optional[int] = None
    schedule_mode: Optional[str] = None
    schedule_config: Optional[Dict] = None
    account_ids: Optional[List[str]] = None
    enabled: Optional[bool] = None


# ========== CRUD ==========

@router.post("", response_model=ApiResponse)
async def create_offline_schedule(
    req: CreateOfflineScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    schedule = await svc.create(current_user.id, req.model_dump())
    return ApiResponse(success=True, message="下架规则创建成功", data={"id": schedule.id})


@router.get("", response_model=ApiResponse)
async def list_offline_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_schedules(user_id=query_user_id, page=page, page_size=page_size)
    return ApiResponse(success=True, message="查询成功", data=data)


@router.get("/{schedule_id}", response_model=ApiResponse)
async def get_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在")
    from app.services.offline_schedule_service import _offline_to_dict
    return ApiResponse(success=True, message="查询成功", data=_offline_to_dict(schedule))


@router.put("/{schedule_id}", response_model=ApiResponse)
async def update_offline_schedule(
    schedule_id: int,
    req: UpdateOfflineScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    schedule = await svc.update(schedule_id, query_user_id, update_data)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    from app.services.offline_schedule_service import _offline_to_dict
    return ApiResponse(success=True, message="规则更新成功", data=_offline_to_dict(schedule))


@router.delete("/{schedule_id}", response_model=ApiResponse)
async def delete_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    ok = await svc.delete(schedule_id, query_user_id)
    return ApiResponse(success=ok, message="规则已删除" if ok else "规则不存在或无权操作")


@router.patch("/{schedule_id}/toggle", response_model=ApiResponse)
async def toggle_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.toggle(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    from app.services.offline_schedule_service import _offline_to_dict
    return ApiResponse(success=True, message=f"规则已{'启用' if schedule.enabled else '禁用'}", data=_offline_to_dict(schedule))


@router.post("/{schedule_id}/trigger", response_model=ApiResponse)
async def trigger_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")

    result = await svc.execute_offline(schedule)
    await svc.advance_schedule(schedule)

    return ApiResponse(success=result.get("success", False), message=result.get("message", ""), data=result)


@router.get("/logs/global", response_model=ApiResponse)
async def list_offline_schedule_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询所有下架规则的执行记录"""
    from sqlalchemy import select, func, desc
    from common.models.offline_schedule_log import OfflineScheduleLog
    from common.models.offline_schedule import OfflineSchedule
    from common.utils.time_utils import safe_isoformat

    query_user_id = None if _is_admin(current_user) else current_user.id

    conds = []
    if query_user_id is not None:
        sub_stmt = select(OfflineSchedule.id).where(OfflineSchedule.user_id == query_user_id)
        conds.append(OfflineScheduleLog.schedule_id.in_(sub_stmt))

    count_stmt = select(func.count()).select_from(OfflineScheduleLog).where(*conds)
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = (
        select(OfflineScheduleLog, OfflineSchedule.name)
        .join(OfflineSchedule, OfflineScheduleLog.schedule_id == OfflineSchedule.id)
        .where(*conds)
        .order_by(desc(OfflineScheduleLog.executed_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()

    list_data = []
    for log, name in rows:
        list_data.append({
            "id": log.id,
            "schedule_id": log.schedule_id,
            "schedule_name": name or f"规则 #{log.schedule_id}",
            "executed_at": safe_isoformat(log.executed_at),
            "status": log.status,
            "total_count": log.total_count,
            "offlined_count": log.offlined_count,
            "offlined_items": log.offlined_items or [],
            "error_message": log.error_message,
        })

    return ApiResponse(success=True, message="查询成功", data={
        "list": list_data,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    })
