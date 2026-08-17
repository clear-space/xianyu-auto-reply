"""
自动下架规则服务

功能：
1. 下架规则 CRUD（创建/查询/更新/删除/开关）
2. next_trigger_at 计算（时间计算统一在 common.utils.schedule_time，本服务直接引用）
3. 执行记录管理（模式与定时发布模块保持一致）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.offline_schedule import OfflineSchedule
from common.models.offline_schedule_log import OfflineScheduleLog
from common.utils.schedule_time import compute_next_trigger as _compute_next_trigger
from common.utils.time_utils import get_beijing_now, safe_isoformat


def _schedule_to_dict(s: OfflineSchedule) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "name": s.name,
        "schedule_mode": s.schedule_mode,
        "schedule_config": s.schedule_config or {},
        "account_ids": s.account_ids or [],
        "offline_days": s.offline_days,
        "no_order_days": s.no_order_days,
        "max_count": s.max_count,
        "enabled": s.enabled,
        "last_triggered_at": safe_isoformat(s.last_triggered_at),
        "next_trigger_at": safe_isoformat(s.next_trigger_at),
        "created_at": safe_isoformat(s.created_at),
        "updated_at": safe_isoformat(s.updated_at),
    }


def _log_to_dict(l: OfflineScheduleLog) -> dict:
    return {
        "id": l.id,
        "schedule_id": l.schedule_id,
        "schedule_name": l.schedule_name,
        "batch_id": l.batch_id,
        "scheduled_at": safe_isoformat(l.scheduled_at),
        "executed_at": safe_isoformat(l.executed_at),
        "status": l.status,
        "total_count": l.total_count,
        "success_count": l.success_count,
        "failed_count": l.failed_count,
        "error_message": l.error_message,
        "detail_json": l.detail_json or {},
        "created_at": safe_isoformat(l.created_at),
        "updated_at": safe_isoformat(l.updated_at),
    }


class OfflineScheduleService:
    """自动下架规则 CRUD 服务"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ========== 规则 CRUD ==========

    async def create(self, user_id: int, data: dict) -> OfflineSchedule:
        """创建下架规则，自动计算 next_trigger_at"""
        schedule = OfflineSchedule(
            user_id=user_id,
            name=data["name"],
            schedule_mode=data.get("schedule_mode", "daily"),
            schedule_config=data.get("schedule_config", {}),
            account_ids=data.get("account_ids", []),
            offline_days=int(data.get("offline_days", 7)),
            no_order_days=int(data.get("no_order_days", 0)),
            max_count=int(data.get("max_count", 10)),
            enabled=data.get("enabled", True),
        )
        schedule.next_trigger_at = _compute_next_trigger(
            schedule.schedule_mode, schedule.schedule_config
        )
        self.session.add(schedule)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def list_schedules(
        self, user_id: int = None, page: int = 1, page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询下架规则"""
        page = max(page, 1)
        page_size = page_size if page_size in (10, 20, 50, 100) else 20

        conds = []
        if user_id is not None:
            conds.append(OfflineSchedule.user_id == user_id)

        count_stmt = select(func.count()).select_from(OfflineSchedule).where(*conds)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(OfflineSchedule)
            .where(*conds)
            .order_by(desc(OfflineSchedule.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        return {
            "list": [_schedule_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    async def get(self, schedule_id: int, user_id: int = None) -> Optional[OfflineSchedule]:
        conds = [OfflineSchedule.id == schedule_id]
        if user_id is not None:
            conds.append(OfflineSchedule.user_id == user_id)
        stmt = select(OfflineSchedule).where(*conds)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update(self, schedule_id: int, user_id: int = None, data: dict = None) -> Optional[OfflineSchedule]:
        """更新规则，重新计算 next_trigger_at"""
        data = data or {}
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return None

        updatable = [
            "name", "schedule_mode", "schedule_config",
            "account_ids", "offline_days", "no_order_days", "max_count",
            "enabled",
        ]
        for field in updatable:
            if field in data and data[field] is not None:
                setattr(schedule, field, data[field])

        # 重新计算下次触发时间
        schedule.next_trigger_at = _compute_next_trigger(
            schedule.schedule_mode, schedule.schedule_config
        )

        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def delete(self, schedule_id: int, user_id: int = None) -> bool:
        """删除规则，同时取消关联的 pending 执行记录"""
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return False
        cancel_stmt = (
            select(OfflineScheduleLog)
            .where(
                OfflineScheduleLog.schedule_id == schedule_id,
                OfflineScheduleLog.status.in_(["pending"]),
            )
        )
        pending_logs = (await self.session.execute(cancel_stmt)).scalars().all()
        for log in pending_logs:
            log.status = "cancelled"
        await self.session.delete(schedule)
        await self.session.commit()
        return True

    async def toggle(self, schedule_id: int, user_id: int = None) -> Optional[OfflineSchedule]:
        """切换启用/禁用状态"""
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return None
        schedule.enabled = not schedule.enabled
        if schedule.enabled:
            # 重新启用时重新计算 next_trigger_at
            schedule.next_trigger_at = _compute_next_trigger(
                schedule.schedule_mode, schedule.schedule_config
            )
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    # ========== 执行日志 ==========

    async def get_running_log(self, schedule_id: int) -> Optional[OfflineScheduleLog]:
        """查询规则当前的 running 执行记录（用于防重复触发）"""
        stmt = (
            select(OfflineScheduleLog)
            .where(
                OfflineScheduleLog.schedule_id == schedule_id,
                OfflineScheduleLog.status == "running",
            )
            .order_by(OfflineScheduleLog.id.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def create_log(
        self, schedule_id: int, scheduled_at: datetime, total_count: int,
        schedule_name: Optional[str] = None,
    ) -> OfflineScheduleLog:
        log = OfflineScheduleLog(
            schedule_id=schedule_id,
            schedule_name=schedule_name,
            scheduled_at=scheduled_at,
            total_count=total_count,
        )
        self.session.add(log)
        await self.session.commit()
        await self.session.refresh(log)
        return log

    async def update_log(
        self, log_id: int, data: dict
    ) -> Optional[OfflineScheduleLog]:
        stmt = select(OfflineScheduleLog).where(OfflineScheduleLog.id == log_id)
        log = (await self.session.execute(stmt)).scalar_one_or_none()
        if not log:
            return None
        for key in (
            "batch_id", "executed_at", "status", "total_count",
            "success_count", "failed_count", "error_message", "detail_json",
        ):
            if key in data and data[key] is not None:
                setattr(log, key, data[key])
        await self.session.commit()
        await self.session.refresh(log)
        return log

    async def list_logs(
        self, schedule_id: int = None, page: int = 1, page_size: int = 20,
        user_id: int = None,
    ) -> Dict[str, Any]:
        """分页查询执行记录"""
        page = max(page, 1)
        page_size = page_size if page_size in (10, 20, 50, 100) else 20

        conds = []
        if schedule_id is not None:
            conds.append(OfflineScheduleLog.schedule_id == schedule_id)
        if user_id is not None:
            sub_stmt = select(OfflineSchedule.id).where(OfflineSchedule.user_id == user_id)
            conds.append(OfflineScheduleLog.schedule_id.in_(sub_stmt))

        count_stmt = select(func.count()).select_from(OfflineScheduleLog).where(*conds)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(OfflineScheduleLog)
            .where(*conds)
            .order_by(desc(OfflineScheduleLog.scheduled_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        return {
            "list": [_log_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }
