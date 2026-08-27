"""
自动下架 API 路由

功能：
1. 下架规则 CRUD（每天/每周，复用模块一的时间配置与校验）
2. 执行历史查询（含保留天数清空）
3. 手动触发（与 scheduler 定时触发共用 OfflineExecutor）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from app.api.routes.publish_schedule import _validate_owned_accounts
from app.services.offline_schedule_service import OfflineScheduleService
from common.models.user import User, UserRole
from common.schemas.common import ApiResponse
from common.utils.schedule_time import validate_schedule_config

router = APIRouter(prefix="/product-publish/offline-schedules", tags=["商品发布-定时任务"])

_SCHEDULE_MODES = {"daily", "weekly"}  # 下架只支持每天/每周


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


# ==================== 请求模型 ====================

class OfflineScheduleConfig(BaseModel):
    """调度时间配置（与定时发布共用结构）"""
    times: Optional[List[str]] = Field(None, description="时间点列表，如 ['08:00', '20:00']")
    days: Optional[List[int]] = Field(None, description="星期几，1=周一，7=周日")
    time_range: Optional[Dict[str, str]] = Field(None, description="时间段，如 {start:'18:00', end:'22:00'}")
    random: bool = Field(False, description="是否在时间段内随机")


class CreateOfflineScheduleRequest(BaseModel):
    """创建下架规则请求"""
    name: str = Field(..., min_length=1, max_length=100, description="规则名称")
    schedule_mode: str = Field("daily", description="重复模式：daily/weekly")
    schedule_config: OfflineScheduleConfig = Field(..., description="时间配置")
    account_ids: List[str] = Field(..., min_length=1, description="闲鱼账号ID列表")
    max_count: int = Field(..., ge=1, description="下架数量上限Z：每个账号每次最多下架Z个商品")
    delist_algorithm_id: Optional[int] = Field(None, description="下架权重算法ID（选品排序；不传=系统默认参数）")


class UpdateOfflineScheduleRequest(BaseModel):
    """更新下架规则请求（所有字段可选）"""
    name: Optional[str] = Field(None, max_length=100)
    schedule_mode: Optional[str] = None
    schedule_config: Optional[OfflineScheduleConfig] = None
    account_ids: Optional[List[str]] = Field(None, min_length=1)
    max_count: Optional[int] = Field(None, ge=1)
    delist_algorithm_id: Optional[int] = Field(None, description="下架权重算法ID；传 None 清除回默认参数")
    enabled: Optional[bool] = None


# ==================== 规则 CRUD ====================

@router.post("", response_model=ApiResponse)
async def create_offline_schedule(
    req: CreateOfflineScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """创建自动下架规则"""
    svc = OfflineScheduleService(session)
    config = req.schedule_config.model_dump(exclude_none=True)

    if req.schedule_mode not in _SCHEDULE_MODES:
        return ApiResponse(success=False, message=f"不支持的重复模式: {req.schedule_mode}（下架仅支持每天/每周）")
    try:
        validate_schedule_config(req.schedule_mode, config)
    except ValueError as e:
        return ApiResponse(success=False, message=str(e))
    error = await _validate_owned_accounts(session, current_user.id, req.account_ids)
    if error:
        return ApiResponse(success=False, message=error)

    try:
        schedule = await svc.create(current_user.id, {
            "name": req.name,
            "schedule_mode": req.schedule_mode,
            "schedule_config": config,
            "account_ids": req.account_ids,
            "max_count": req.max_count,
            "delist_algorithm_id": req.delist_algorithm_id,
        })
        return ApiResponse(success=True, message="下架规则创建成功", data=await svc.to_dict(schedule))
    except Exception as e:
        await session.rollback()
        return ApiResponse(success=False, message=f"创建失败: {str(e)}")


@router.get("", response_model=ApiResponse)
async def list_offline_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询下架规则列表"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_schedules(user_id=query_user_id, page=page, page_size=page_size)
    for item in data["list"]:
        item["account_count"] = len(item.get("account_ids", []))
    return ApiResponse(success=True, message="查询成功", data=data)


@router.get("/{schedule_id}", response_model=ApiResponse)
async def get_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询单条下架规则详情"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在")
    return ApiResponse(success=True, message="查询成功", data=await svc.to_dict(schedule))


@router.put("/{schedule_id}", response_model=ApiResponse)
async def update_offline_schedule(
    schedule_id: int,
    req: UpdateOfflineScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """更新下架规则（合并校验更新后的有效状态）"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    current = await svc.get(schedule_id, query_user_id)
    if not current:
        return ApiResponse(success=False, message="规则不存在或无权操作")

    update_data = {
        k: v for k, v in req.model_dump(exclude={"schedule_config"}).items() if v is not None
    }
    # 允许显式清除下架算法（None 值）
    if "delist_algorithm_id" in req.model_fields_set and req.delist_algorithm_id is None:
        update_data["delist_algorithm_id"] = None

    eff_mode = update_data.get("schedule_mode", current.schedule_mode)
    eff_config = (
        req.schedule_config.model_dump(exclude_none=True)
        if req.schedule_config is not None
        else (current.schedule_config or {})
    )
    eff_account_ids = update_data.get("account_ids") or current.account_ids or []
    eff_max_count = update_data.get("max_count", current.max_count)

    if eff_mode not in _SCHEDULE_MODES:
        return ApiResponse(success=False, message=f"不支持的重复模式: {eff_mode}（下架仅支持每天/每周）")
    try:
        validate_schedule_config(eff_mode, eff_config)
    except ValueError as e:
        return ApiResponse(success=False, message=str(e))
    if eff_max_count < 1:
        return ApiResponse(success=False, message="筛选参数不合法：下架上限≥1")

    if "account_ids" in update_data:
        error = await _validate_owned_accounts(session, current.user_id, eff_account_ids)
        if error:
            return ApiResponse(success=False, message=error)

    if req.schedule_config is not None:
        update_data["schedule_config"] = eff_config

    schedule = await svc.update(schedule_id, query_user_id, update_data)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    return ApiResponse(success=True, message="规则更新成功", data=await svc.to_dict(schedule))


@router.delete("/{schedule_id}", response_model=ApiResponse)
async def delete_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """删除下架规则（同时取消关联的 pending 执行记录）"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    ok = await svc.delete(schedule_id, query_user_id)
    if not ok:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    return ApiResponse(success=True, message="规则已删除")


@router.patch("/{schedule_id}/toggle", response_model=ApiResponse)
async def toggle_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """切换规则启用/禁用状态"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.toggle(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")
    return ApiResponse(
        success=True,
        message=f"规则已{'启用' if schedule.enabled else '禁用'}",
        data=await svc.to_dict(schedule),
    )


# ==================== 手动触发 ====================

@router.post("/{schedule_id}/trigger", response_model=ApiResponse)
async def trigger_offline_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """手动立即触发一次下架规则（不影响原调度周期）"""
    import asyncio
    import uuid

    from loguru import logger

    from app.services.offline_executor import OfflineExecutor

    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")

    # 防重复触发：存在 running 执行记录时拒绝
    if await svc.get_running_log(schedule_id):
        return ApiResponse(success=False, message="该规则正在执行中，请稍后再试")

    batch_id = str(uuid.uuid4())
    from common.utils.time_utils import get_beijing_now

    log_entry = await svc.create_log(
        schedule_id, get_beijing_now(), 0, schedule_name=schedule.name
    )
    await svc.update_log(log_entry.id, {"batch_id": batch_id, "status": "running"})

    schedule_data = {
        "id": schedule.id,
        "account_ids": list(schedule.account_ids or []),
        "max_count": schedule.max_count,
        "delist_algorithm_id": schedule.delist_algorithm_id,
    }
    asyncio.create_task(
        OfflineExecutor.run(
            user_id=schedule.user_id,  # 以规则属主身份执行（管理员可触发他人规则）
            schedule_data=schedule_data,
            batch_id=batch_id,
            schedule_log_id=log_entry.id,
        )
    )

    logger.info(
        f"[定时下架] 手动触发 schedule_id={schedule_id}, batch_id={batch_id}, "
        f"账号={len(schedule_data['account_ids'])}"
    )
    return ApiResponse(
        success=True,
        message=f"已触发自动下架，{len(schedule_data['account_ids'])} 个账号",
        data={"batch_id": batch_id, "log_id": log_entry.id},
    )


# ==================== 执行记录 ====================

@router.get("/{schedule_id}/logs", response_model=ApiResponse)
async def list_offline_schedule_logs(
    schedule_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询某规则的历史执行记录"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权查看")
    data = await svc.list_logs(schedule_id=schedule_id, page=page, page_size=page_size)
    return ApiResponse(success=True, message="查询成功", data=data)


@router.get("/logs/global", response_model=ApiResponse)
async def list_all_offline_schedule_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询所有下架规则的执行历史（全局视图）"""
    svc = OfflineScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_logs(schedule_id=None, page=page, page_size=page_size, user_id=query_user_id)
    return ApiResponse(success=True, message="查询成功", data=data)


@router.delete("/logs/clear", response_model=ApiResponse)
@router.post("/logs/clear", response_model=ApiResponse)
async def clear_offline_schedule_logs(
    days: int | None = Query(default=None, ge=0, description="保留最近N天的执行记录；0或不传则清空全部"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """清空下架执行日志（可指定保留最近 N 天，前端默认 10 天；不传或传 0 则清空全部）"""
    from datetime import timedelta

    from loguru import logger
    from sqlalchemy import delete

    from common.models.offline_schedule import OfflineSchedule
    from common.models.offline_schedule_log import OfflineScheduleLog
    from common.utils.time_utils import get_beijing_now

    query_user_id = None if _is_admin(current_user) else current_user.id

    try:
        conds = []
        if query_user_id is not None:
            sub_stmt = select(OfflineSchedule.id).where(OfflineSchedule.user_id == query_user_id)
            conds.append(OfflineScheduleLog.schedule_id.in_(sub_stmt))

        if days and days > 0:
            cutoff = get_beijing_now() - timedelta(days=days)
            conds.append(OfflineScheduleLog.created_at < cutoff)
            scope_label = f"{days}天前的"
        else:
            scope_label = "全部"

        stmt = delete(OfflineScheduleLog).where(*conds)
        result = await session.execute(stmt)
        await session.commit()

        deleted_count = result.rowcount or 0
        logger.info(f"[定时下架] 用户 {current_user.id} 已清空 {deleted_count} 条{scope_label}执行日志")
        return ApiResponse(
            success=True,
            message=f"已清空 {deleted_count} 条{scope_label}执行日志",
            data={"deleted_count": deleted_count},
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"[定时下架] 清空执行日志失败: {e}")
        return ApiResponse(success=False, message=f"清空执行日志失败: {str(e)}")
