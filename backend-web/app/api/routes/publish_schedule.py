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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from app.services.publish_schedule_service import PublishScheduleService
from common.models.user import User, UserRole
from common.schemas.common import ApiResponse
from common.utils.schedule_time import validate_schedule_config as _validate_schedule_config

router = APIRouter(prefix="/product-publish/schedules", tags=["商品发布-定时管理"])


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


# ==================== 校验工具 ====================

_SCHEDULE_MODES = {"once", "daily", "weekly"}
_PUBLISH_MODES = {"specified", "random"}


def _validate_publish_fields(
    publish_mode: str, random_count: Optional[int], deduplicate_enabled: bool, material_count: int
) -> None:
    """校验发布模式相关字段，非法时抛出 ValueError"""
    if publish_mode not in _PUBLISH_MODES:
        raise ValueError(f"不支持的发布模式: {publish_mode}")
    if publish_mode == "random":
        if not random_count or random_count < 1:
            raise ValueError("随机发布模式必须设置随机发布数量（至少1条）")
        if random_count > material_count:
            raise ValueError(f"随机发布数量不能超过所选素材数（{material_count}）")
    else:
        if deduplicate_enabled:
            raise ValueError("去重开关仅随机发布模式可用")


async def _validate_owned_accounts(session: AsyncSession, user_id: int, account_ids: List[str]) -> Optional[str]:
    """校验账号归属，返回错误消息（None 表示全部合法）"""
    from common.models.xy_account import XYAccount

    unique_ids = list(dict.fromkeys(account_ids))
    stmt = select(XYAccount.account_id).where(
        XYAccount.owner_id == user_id,
        XYAccount.account_id.in_(unique_ids),
    )
    owned = {row[0] for row in (await session.execute(stmt)).all()}
    missing = [aid for aid in unique_ids if aid not in owned]
    if missing:
        shown = "、".join(str(x) for x in missing[:5])
        more = f" 等{len(missing)}个" if len(missing) > 5 else ""
        return f"以下账号不存在或不属于当前用户: {shown}{more}"
    return None


async def _validate_owned_materials(session: AsyncSession, user_id: int, material_ids: List[int]) -> Optional[str]:
    """校验素材归属，返回错误消息（None 表示全部合法）"""
    from app.services.product_publish_service import ProductMaterialService

    mat_svc = ProductMaterialService(session)
    materials = await mat_svc.list_by_ids(material_ids, user_id)
    found = {m.id for m in materials}
    missing = [mid for mid in dict.fromkeys(material_ids) if mid not in found]
    if missing:
        shown = "、".join(str(x) for x in missing[:5])
        more = f" 等{len(missing)}个" if len(missing) > 5 else ""
        return f"以下素材不存在或不属于当前用户: {shown}{more}"
    return None


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
    schedule_config: ScheduleConfig = Field(..., description="时间配置")
    account_ids: List[str] = Field(..., min_length=1, description="闲鱼账号ID列表")
    material_ids: List[int] = Field(..., min_length=1, description="素材ID列表")
    publish_mode: str = Field("specified", description="发布模式：specified-指定发布，random-随机发布")
    random_count: Optional[int] = Field(None, description="随机发布数量（随机模式必填）")
    deduplicate_enabled: bool = Field(False, description="去重开关（仅随机模式可用）")


class UpdateScheduleRequest(BaseModel):
    """更新定时规则请求（所有字段可选）"""
    name: Optional[str] = Field(None, max_length=100)
    schedule_mode: Optional[str] = None
    schedule_config: Optional[ScheduleConfig] = None
    account_ids: Optional[List[str]] = Field(None, min_length=1)
    material_ids: Optional[List[int]] = Field(None, min_length=1)
    publish_mode: Optional[str] = None
    random_count: Optional[int] = None
    deduplicate_enabled: Optional[bool] = None
    enabled: Optional[bool] = None


class BatchDeleteLogItem(BaseModel):
    """待删除的执行记录（类别 + 记录ID，发布/下架两张表ID可能重复）"""
    rule_type: str = Field(..., description="规则类别：publish-发布, offline-下架")
    log_id: int = Field(..., description="执行记录ID")


class BatchDeleteScheduleLogsRequest(BaseModel):
    items: List[BatchDeleteLogItem] = Field(..., min_length=1, description="待删除的执行记录列表")


# ==================== 规则 CRUD ====================

@router.post("", response_model=ApiResponse)
async def create_schedule(
    req: CreateScheduleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """创建定时发布规则"""
    svc = PublishScheduleService(session)
    config = req.schedule_config.model_dump(exclude_none=True)

    # 校验：重复模式与时间配置
    if req.schedule_mode not in _SCHEDULE_MODES:
        return ApiResponse(success=False, message=f"不支持的重复模式: {req.schedule_mode}")
    try:
        _validate_schedule_config(req.schedule_mode, config)
        _validate_publish_fields(req.publish_mode, req.random_count, req.deduplicate_enabled, len(req.material_ids))
    except ValueError as e:
        return ApiResponse(success=False, message=str(e))
    # 校验：账号归属 / 素材归属
    error = await _validate_owned_accounts(session, current_user.id, req.account_ids)
    if error:
        return ApiResponse(success=False, message=error)
    error = await _validate_owned_materials(session, current_user.id, req.material_ids)
    if error:
        return ApiResponse(success=False, message=error)

    # 指定发布模式不允许带随机配置
    publish_mode = req.publish_mode
    random_count = req.random_count
    deduplicate_enabled = req.deduplicate_enabled
    if publish_mode == "specified":
        random_count = None
        deduplicate_enabled = False

    try:
        schedule = await svc.create(current_user.id, {
            "name": req.name,
            "schedule_mode": req.schedule_mode,
            "schedule_config": config,
            "account_ids": req.account_ids,
            "material_ids": req.material_ids,
            "publish_mode": publish_mode,
            "random_count": random_count,
            "deduplicate_enabled": deduplicate_enabled,
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


# ==================== 实时进度查询 ====================

@router.get("/active-progress", response_model=ApiResponse)
async def get_active_schedule_progress(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询当前用户所有正在执行的定时发布任务的实时进度

    用于前端轮询展示定时任务的批量发布进度面板。
    返回 running 状态的执行记录及其关联的 batch 进度数据。
    """
    from datetime import timedelta

    from sqlalchemy import func

    from common.models.publish_log import PublishLog
    from common.models.publish_schedule import PublishSchedule
    from common.models.publish_schedule_log import PublishScheduleLog
    from common.utils.time_utils import get_beijing_now, safe_isoformat
    from app.services.publish_batch_status_service import PublishBatchStatusService

    query_user_id = None if _is_admin(current_user) else current_user.id

    # 查询最近24h内 status=running 的执行记录
    since = get_beijing_now() - timedelta(hours=24)
    conds = [
        PublishScheduleLog.status == "running",
        PublishScheduleLog.scheduled_at >= since,
    ]
    if query_user_id is not None:
        # 通过关联 PublishSchedule 限制用户
        conds.append(PublishSchedule.user_id == query_user_id)

    stmt = (
        select(
            PublishScheduleLog.id,
            PublishScheduleLog.schedule_id,
            PublishScheduleLog.batch_id,
            PublishScheduleLog.scheduled_at,
            PublishSchedule.name,
        )
        .join(PublishSchedule, PublishScheduleLog.schedule_id == PublishSchedule.id)
        .where(*conds)
        .order_by(PublishScheduleLog.scheduled_at.asc())
    )
    rows = (await session.execute(stmt)).all()

    if not rows:
        return ApiResponse(success=True, message="没有进行中的任务", data={"tasks": []})

    tasks = []
    for row in rows:
        schedule_log_id = row[0]
        schedule_id = row[1]
        batch_id = row[2]
        scheduled_at = row[3]
        schedule_name = row[4] or f"规则 #{schedule_id}"

        progress = None
        if batch_id:
            # 统计 batch 进度（复刻 get_batch_status 的查询逻辑）
            status_stmt = select(
                PublishLog.status,
                func.count().label("cnt"),
            ).where(
                PublishLog.batch_id == batch_id,
            )
            # 非管理员只查自己的
            if query_user_id is not None:
                status_stmt = status_stmt.where(PublishLog.user_id == query_user_id)

            status_rows = (await session.execute(status_stmt)).all()
            counts = {r.status: r.cnt for r in status_rows}

            total = sum(counts.values())
            success = int(counts.get("success", 0))
            failed = int(counts.get("failed", 0))
            publishing = int(counts.get("publishing", 0))
            pending = int(counts.get("pending", 0))

            # 从缓存快照获取账号级数据
            batch_snapshot = await PublishBatchStatusService.get_batch_snapshot(batch_id)
            account_statuses: List[Dict[str, Any]] = []
            if batch_snapshot:
                material_count = int(batch_snapshot.get("material_count") or 0)
                account_order = batch_snapshot.get("account_order") or []
                account_sync_map = batch_snapshot.get("accounts") or {}
                for account_id in account_order:
                    sync_info = account_sync_map.get(account_id, {})
                    account_statuses.append({
                        "account_id": account_id,
                        "total": material_count,
                        "success": 0,
                        "failed": 0,
                        "publishing": 0,
                        "pending": material_count,
                        "sync_status": sync_info.get("sync_status", "pending"),
                        "sync_message": sync_info.get("sync_message", "等待该账号发布完成后自动获取商品"),
                        "sync_total_count": int(sync_info.get("sync_total_count") or 0),
                        "sync_saved_count": int(sync_info.get("sync_saved_count") or 0),
                    })

            finished = total > 0 and (publishing + pending) == 0

            progress = {
                "total": total,
                "success": success,
                "failed": failed,
                "publishing": publishing,
                "pending": pending,
                "finished": finished,
                "account_statuses": account_statuses,
            }

            # 若 batch 已完成，更新 schedule log（兜底）
            if finished:
                try:
                    log_stmt = select(PublishScheduleLog).where(PublishScheduleLog.id == schedule_log_id)
                    log_entry = (await session.execute(log_stmt)).scalar_one_or_none()
                    if log_entry and log_entry.status == "running":
                        log_entry.status = "failed" if failed > 0 else "completed"
                        log_entry.executed_at = get_beijing_now()
                        log_entry.success_count = success
                        log_entry.failed_count = failed
                        await session.commit()
                except Exception:
                    pass  # 非关键路径，静默处理

        tasks.append({
            "schedule_log_id": schedule_log_id,
            "schedule_id": schedule_id,
            "schedule_name": schedule_name,
            "batch_id": batch_id,
            "scheduled_at": safe_isoformat(scheduled_at) if scheduled_at else None,
            "progress": progress,
        })

    return ApiResponse(success=True, message="查询成功", data={"tasks": tasks})


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
    """更新定时规则（合并校验更新后的有效状态）"""
    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    current = await svc.get(schedule_id, query_user_id)
    if not current:
        return ApiResponse(success=False, message="规则不存在或无权操作")

    update_data = {
        k: v for k, v in req.model_dump(exclude={"schedule_config"}).items() if v is not None
    }

    # 合并更新后的有效值做整体校验
    eff_mode = update_data.get("schedule_mode", current.schedule_mode)
    eff_config = (
        req.schedule_config.model_dump(exclude_none=True)
        if req.schedule_config is not None
        else (current.schedule_config or {})
    )
    eff_account_ids = update_data.get("account_ids") or current.account_ids or []
    eff_material_ids = update_data.get("material_ids") or current.material_ids or []
    eff_publish_mode = update_data.get("publish_mode", current.publish_mode or "specified")
    eff_random_count = update_data.get("random_count", current.random_count)
    eff_dedup = update_data.get("deduplicate_enabled", bool(current.deduplicate_enabled))

    if eff_mode not in _SCHEDULE_MODES:
        return ApiResponse(success=False, message=f"不支持的重复模式: {eff_mode}")
    try:
        _validate_schedule_config(eff_mode, eff_config)
    except ValueError as e:
        return ApiResponse(success=False, message=str(e))

    if eff_publish_mode == "specified":
        # 切到指定发布：清掉随机配置
        eff_random_count = None
        eff_dedup = False
    else:
        if eff_publish_mode != "random":
            return ApiResponse(success=False, message=f"不支持的发布模式: {eff_publish_mode}")
        if not eff_random_count or eff_random_count < 1:
            return ApiResponse(success=False, message="随机发布模式必须设置随机发布数量（至少1条）")
        if eff_random_count > len(eff_material_ids):
            return ApiResponse(
                success=False, message=f"随机发布数量不能超过所选素材数（{len(eff_material_ids)}）"
            )

    # 校验：账号归属 / 素材归属（按规则属主校验；管理员可编辑他人规则）
    if "account_ids" in update_data:
        error = await _validate_owned_accounts(session, current.user_id, eff_account_ids)
        if error:
            return ApiResponse(success=False, message=error)
    if "material_ids" in update_data:
        error = await _validate_owned_materials(session, current.user_id, eff_material_ids)
        if error:
            return ApiResponse(success=False, message=error)

    update_data["publish_mode"] = eff_publish_mode
    update_data["random_count"] = eff_random_count
    update_data["deduplicate_enabled"] = eff_dedup
    if req.schedule_config is not None:
        update_data["schedule_config"] = eff_config

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
    """手动立即触发一次定时规则（不影响原调度周期；once 模式触发后自动禁用）"""
    import asyncio
    import uuid

    from loguru import logger

    from app.services.product_publish_service import ProductMaterialService
    from app.services.publish_batch_status_service import PublishBatchStatusService
    from app.services.scheduled_publish_executor import ScheduledPublishExecutor

    svc = PublishScheduleService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    schedule = await svc.get(schedule_id, query_user_id)
    if not schedule:
        return ApiResponse(success=False, message="规则不存在或无权操作")

    # 防重复触发：存在 running 执行记录时拒绝
    if await svc.get_running_log(schedule_id):
        return ApiResponse(success=False, message="该规则正在执行中，请稍后再试")

    # 预检素材（按规则属主校验；管理员可触发他人规则）
    mat_svc = ProductMaterialService(session)
    materials = await mat_svc.list_by_ids(schedule.material_ids, schedule.user_id)
    if not materials:
        return ApiResponse(success=False, message="没有找到有效的素材")

    account_ids = list(schedule.account_ids or [])
    batch_id = str(uuid.uuid4())

    # 创建执行记录
    from common.utils.time_utils import get_beijing_now

    log_entry = await svc.create_log(
        schedule_id, get_beijing_now(), len(account_ids) * len(materials),
        schedule_name=schedule.name,
    )
    await svc.update_log(log_entry.id, {"batch_id": batch_id, "status": "running"})

    # 初始化 batch 状态
    await PublishBatchStatusService.init_batch(
        batch_id=batch_id, account_ids=account_ids, material_count=len(materials),
    )

    # 后台执行：与 scheduler 定时触发共用同一份执行器（随机选料/去重/自动补发）
    schedule_data = {
        "id": schedule.id,
        "account_ids": account_ids,
        "material_ids": list(schedule.material_ids or []),
        "publish_mode": schedule.publish_mode or "specified",
        "random_count": schedule.random_count,
        "deduplicate_enabled": bool(schedule.deduplicate_enabled),
    }
    asyncio.create_task(
        ScheduledPublishExecutor.run(
            user_id=schedule.user_id,  # 以规则属主身份执行（管理员可触发他人规则）
            schedule_data=schedule_data,
            batch_id=batch_id,
            schedule_log_id=log_entry.id,
        )
    )

    # once 模式：手动触发即视为执行一次，完成后规则自动禁用
    if schedule.schedule_mode == "once":
        await svc.update(schedule_id, query_user_id, {"enabled": False})

    logger.info(
        f"[定时发布] 手动触发 schedule_id={schedule_id}, batch_id={batch_id}, "
        f"{len(account_ids)} 账号 × {len(schedule.material_ids or [])} 素材"
    )

    return ApiResponse(
        success=True,
        message=f"已触发批量发布，{len(account_ids)} 账号 × {len(schedule.material_ids or [])} 素材",
        data={"batch_id": batch_id, "log_id": log_entry.id},
    )


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


@router.get("/history/global", response_model=ApiResponse)
async def list_unified_schedule_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """合并查询定时发布与自动下架的执行历史（定时历史统一视图，带规则类别）"""
    from sqlalchemy import func, literal, union_all

    from common.models.offline_schedule import OfflineSchedule
    from common.models.offline_schedule_log import OfflineScheduleLog
    from common.models.publish_schedule import PublishSchedule
    from common.models.publish_schedule_log import PublishScheduleLog
    from common.utils.time_utils import safe_isoformat

    page = max(page, 1)
    page_size = page_size if page_size in (10, 20, 50, 100) else 20
    query_user_id = None if _is_admin(current_user) else current_user.id

    pub_conds = []
    off_conds = []
    if query_user_id is not None:
        pub_conds.append(
            PublishScheduleLog.schedule_id.in_(
                select(PublishSchedule.id).where(PublishSchedule.user_id == query_user_id)
            )
        )
        off_conds.append(
            OfflineScheduleLog.schedule_id.in_(
                select(OfflineSchedule.id).where(OfflineSchedule.user_id == query_user_id)
            )
        )

    pub_stmt = select(
        literal("publish").label("rule_type"),
        PublishScheduleLog.id.label("log_id"),
        PublishScheduleLog.schedule_id.label("schedule_id"),
        PublishScheduleLog.schedule_name.label("schedule_name"),
        PublishScheduleLog.batch_id.label("batch_id"),
        PublishScheduleLog.scheduled_at.label("scheduled_at"),
        PublishScheduleLog.executed_at.label("executed_at"),
        PublishScheduleLog.status.label("status"),
        PublishScheduleLog.total_count.label("total_count"),
        PublishScheduleLog.success_count.label("success_count"),
        PublishScheduleLog.failed_count.label("failed_count"),
        PublishScheduleLog.error_message.label("error_message"),
        PublishScheduleLog.detail_json.label("detail_json"),
        PublishScheduleLog.created_at.label("created_at"),
    ).where(*pub_conds)

    off_stmt = select(
        literal("offline").label("rule_type"),
        OfflineScheduleLog.id.label("log_id"),
        OfflineScheduleLog.schedule_id.label("schedule_id"),
        OfflineScheduleLog.schedule_name.label("schedule_name"),
        OfflineScheduleLog.batch_id.label("batch_id"),
        OfflineScheduleLog.scheduled_at.label("scheduled_at"),
        OfflineScheduleLog.executed_at.label("executed_at"),
        OfflineScheduleLog.status.label("status"),
        OfflineScheduleLog.total_count.label("total_count"),
        OfflineScheduleLog.success_count.label("success_count"),
        OfflineScheduleLog.failed_count.label("failed_count"),
        OfflineScheduleLog.error_message.label("error_message"),
        OfflineScheduleLog.detail_json.label("detail_json"),
        OfflineScheduleLog.created_at.label("created_at"),
    ).where(*off_conds)

    union_sub = union_all(pub_stmt, off_stmt).subquery()

    count_stmt = select(func.count()).select_from(union_sub)
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = (
        select(union_sub)
        .order_by(union_sub.c.scheduled_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()

    list_data = [
        {
            "rule_type": r.rule_type,
            "log_id": r.log_id,
            "schedule_id": r.schedule_id,
            "schedule_name": r.schedule_name,
            "batch_id": r.batch_id,
            "scheduled_at": safe_isoformat(r.scheduled_at),
            "executed_at": safe_isoformat(r.executed_at),
            "status": r.status,
            "total_count": r.total_count,
            "success_count": r.success_count,
            "failed_count": r.failed_count,
            "error_message": r.error_message,
            "detail_json": r.detail_json or {},
            "created_at": safe_isoformat(r.created_at),
        }
        for r in rows
    ]
    return ApiResponse(
        success=True,
        message="查询成功",
        data={
            "list": list_data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        },
    )


@router.post("/logs/batch-delete", response_model=ApiResponse)
async def batch_delete_schedule_logs(
    req: BatchDeleteScheduleLogsRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """批量删除定时历史执行记录（发布与下架两类，按类别+记录ID定位）"""
    from loguru import logger
    from sqlalchemy import delete

    from common.models.offline_schedule import OfflineSchedule
    from common.models.offline_schedule_log import OfflineScheduleLog
    from common.models.publish_schedule import PublishSchedule
    from common.models.publish_schedule_log import PublishScheduleLog

    query_user_id = None if _is_admin(current_user) else current_user.id

    pub_ids = [it.log_id for it in req.items if it.rule_type == "publish"]
    off_ids = [it.log_id for it in req.items if it.rule_type == "offline"]

    try:
        deleted = 0
        if pub_ids:
            conds = [PublishScheduleLog.id.in_(pub_ids)]
            if query_user_id is not None:
                conds.append(
                    PublishScheduleLog.schedule_id.in_(
                        select(PublishSchedule.id).where(PublishSchedule.user_id == query_user_id)
                    )
                )
            result = await session.execute(delete(PublishScheduleLog).where(*conds))
            deleted += result.rowcount or 0
        if off_ids:
            conds = [OfflineScheduleLog.id.in_(off_ids)]
            if query_user_id is not None:
                conds.append(
                    OfflineScheduleLog.schedule_id.in_(
                        select(OfflineSchedule.id).where(OfflineSchedule.user_id == query_user_id)
                    )
                )
            result = await session.execute(delete(OfflineScheduleLog).where(*conds))
            deleted += result.rowcount or 0

        await session.commit()
        logger.info(
            f"[定时历史] 用户 {current_user.id} 批量删除 {deleted} 条执行记录"
            f"（发布 {len(pub_ids)} 条，下架 {len(off_ids)} 条）"
        )
        return ApiResponse(
            success=True,
            message=f"已删除 {deleted} 条执行记录",
            data={"deleted_count": deleted},
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"[定时历史] 批量删除执行记录失败: {e}")
        return ApiResponse(success=False, message=f"批量删除失败: {str(e)}")


@router.delete("/logs/clear", response_model=ApiResponse)
@router.post("/logs/clear", response_model=ApiResponse)
async def clear_schedule_logs(
    days: int | None = Query(default=None, ge=0, description="保留最近N天的执行记录；0或不传则清空全部"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """清空定时发布执行日志（可指定保留最近 N 天，前端默认 10 天；不传或传 0 则清空全部）"""
    from datetime import timedelta

    from loguru import logger
    from sqlalchemy import delete, select

    from common.models.publish_schedule import PublishSchedule
    from common.models.publish_schedule_log import PublishScheduleLog
    from common.utils.time_utils import get_beijing_now

    query_user_id = None if _is_admin(current_user) else current_user.id

    try:
        conds = []
        if query_user_id is not None:
            # 通过子查询限制：只清空该用户关联的执行记录
            sub_stmt = select(PublishSchedule.id).where(PublishSchedule.user_id == query_user_id)
            conds.append(PublishScheduleLog.schedule_id.in_(sub_stmt))

        if days and days > 0:
            cutoff = get_beijing_now() - timedelta(days=days)
            conds.append(PublishScheduleLog.created_at < cutoff)
            scope_label = f"{days}天前的"
        else:
            scope_label = "全部"

        stmt = delete(PublishScheduleLog).where(*conds)
        result = await session.execute(stmt)
        await session.commit()

        deleted_count = result.rowcount or 0
        logger.info(f"[定时发布] 用户 {current_user.id} 已清空 {deleted_count} 条{scope_label}执行日志")
        return ApiResponse(
            success=True,
            message=f"已清空 {deleted_count} 条{scope_label}执行日志",
            data={"deleted_count": deleted_count},
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"[定时发布] 清空执行日志失败: {e}")
        return ApiResponse(success=False, message=f"清空执行日志失败: {str(e)}")
