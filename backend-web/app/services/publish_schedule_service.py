"""
定时发布规则服务

功能：
1. 定时规则 CRUD（创建/查询/更新/删除/开关）
2. next_trigger_at 计算（时间计算统一在 common.utils.schedule_time，本服务直接引用）
3. 执行记录管理
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.publish_schedule import PublishSchedule
from common.models.publish_schedule_log import PublishScheduleLog
from common.utils.schedule_time import compute_next_trigger as _compute_next_trigger
from common.utils.time_utils import get_beijing_now, safe_isoformat


def _schedule_to_dict(s: PublishSchedule) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "name": s.name,
        "schedule_mode": s.schedule_mode,
        "schedule_config": s.schedule_config or {},
        "account_ids": s.account_ids or [],
        "material_ids": s.material_ids or [],
        "publish_mode": s.publish_mode or "specified",
        "random_count": s.random_count,
        "deduplicate_enabled": bool(s.deduplicate_enabled),
        "enabled": s.enabled,
        "last_triggered_at": safe_isoformat(s.last_triggered_at),
        "next_trigger_at": safe_isoformat(s.next_trigger_at),
        "created_at": safe_isoformat(s.created_at),
        "updated_at": safe_isoformat(s.updated_at),
    }


def _log_to_dict(l: PublishScheduleLog) -> dict:
    return {
        "id": l.id,
        "schedule_id": l.schedule_id,
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


class PublishScheduleService:
    """定时发布规则 CRUD 服务"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ========== 规则 CRUD ==========

    async def create(self, user_id: int, data: dict) -> PublishSchedule:
        """创建定时规则，自动计算 next_trigger_at"""
        schedule = PublishSchedule(
            user_id=user_id,
            name=data["name"],
            schedule_mode=data.get("schedule_mode", "daily"),
            schedule_config=data.get("schedule_config", {}),
            account_ids=data.get("account_ids", []),
            material_ids=data.get("material_ids", []),
            publish_mode=data.get("publish_mode", "specified"),
            random_count=data.get("random_count"),
            deduplicate_enabled=bool(data.get("deduplicate_enabled", False)),
            enabled=data.get("enabled", True),
        )
        # 计算首次触发时间
        schedule.next_trigger_at = _compute_next_trigger(
            schedule.schedule_mode, schedule.schedule_config
        )
        # once 且时间已过的情况
        if schedule.next_trigger_at is None and schedule.schedule_mode == "once":
            schedule.enabled = False

        self.session.add(schedule)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def list_schedules(
        self, user_id: int = None, page: int = 1, page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询定时规则"""
        page = max(page, 1)
        page_size = page_size if page_size in (10, 20, 50, 100) else 20

        conds = []
        if user_id is not None:
            conds.append(PublishSchedule.user_id == user_id)

        count_stmt = select(func.count()).select_from(PublishSchedule).where(*conds)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(PublishSchedule)
            .where(*conds)
            .order_by(desc(PublishSchedule.created_at))
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

    async def get(self, schedule_id: int, user_id: int = None) -> Optional[PublishSchedule]:
        conds = [PublishSchedule.id == schedule_id]
        if user_id is not None:
            conds.append(PublishSchedule.user_id == user_id)
        stmt = select(PublishSchedule).where(*conds)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update(self, schedule_id: int, user_id: int = None, data: dict = None) -> Optional[PublishSchedule]:
        """更新规则，重新计算 next_trigger_at"""
        data = data or {}
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return None

        updatable = [
            "name", "schedule_mode", "schedule_config",
            "account_ids", "material_ids",
            "publish_mode", "random_count", "deduplicate_enabled",
            "enabled",
        ]
        for field in updatable:
            if field in data and data[field] is not None:
                setattr(schedule, field, data[field])

        # 指定发布模式下清掉随机配置（None 值不会被上面的通用循环处理）
        if schedule.publish_mode == "specified":
            schedule.random_count = None
            schedule.deduplicate_enabled = False

        # 重新计算下次触发时间
        schedule.next_trigger_at = _compute_next_trigger(
            schedule.schedule_mode, schedule.schedule_config
        )
        if schedule.next_trigger_at is None and schedule.schedule_mode == "once":
            schedule.enabled = False

        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def delete(self, schedule_id: int, user_id: int = None) -> bool:
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return False
        # 同时取消关联的 pending 执行记录
        cancel_stmt = (
            select(PublishScheduleLog)
            .where(
                PublishScheduleLog.schedule_id == schedule_id,
                PublishScheduleLog.status.in_(["pending"]),
            )
        )
        pending_logs = (await self.session.execute(cancel_stmt)).scalars().all()
        for log in pending_logs:
            log.status = "cancelled"
        await self.session.delete(schedule)
        await self.session.commit()
        return True

    async def toggle(self, schedule_id: int, user_id: int = None) -> Optional[PublishSchedule]:
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
            # once 且时间已过：无法再触发，保持禁用（否则规则永远挂在列表里）
            if schedule.next_trigger_at is None and schedule.schedule_mode == "once":
                schedule.enabled = False
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    # ========== 执行日志 ==========

    async def get_running_log(self, schedule_id: int) -> Optional[PublishScheduleLog]:
        """查询规则当前的 running 执行记录（用于防重复触发）"""
        stmt = (
            select(PublishScheduleLog)
            .where(
                PublishScheduleLog.schedule_id == schedule_id,
                PublishScheduleLog.status == "running",
            )
            .order_by(PublishScheduleLog.id.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def create_log(self, schedule_id: int, scheduled_at: datetime, total_count: int) -> PublishScheduleLog:
        log = PublishScheduleLog(
            schedule_id=schedule_id,
            scheduled_at=scheduled_at,
            total_count=total_count,
        )
        self.session.add(log)
        await self.session.commit()
        await self.session.refresh(log)
        return log

    async def update_log(
        self, log_id: int, data: dict
    ) -> Optional[PublishScheduleLog]:
        stmt = select(PublishScheduleLog).where(PublishScheduleLog.id == log_id)
        log = (await self.session.execute(stmt)).scalar_one_or_none()
        if not log:
            return None
        for key in ("batch_id", "executed_at", "status", "total_count", "success_count", "failed_count", "error_message"):
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
            conds.append(PublishScheduleLog.schedule_id == schedule_id)
        if user_id is not None:
            # 需要通过 schedule_id 关联到 user
            # 子查询：获取该用户的 schedule ids
            sub_stmt = select(PublishSchedule.id).where(PublishSchedule.user_id == user_id)
            conds.append(PublishScheduleLog.schedule_id.in_(sub_stmt))

        count_stmt = select(func.count()).select_from(PublishScheduleLog).where(*conds)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(PublishScheduleLog)
            .where(*conds)
            .order_by(desc(PublishScheduleLog.scheduled_at))
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

    async def cancel_pending_logs(self, schedule_id: int) -> int:
        """取消某规则所有的 pending 执行记录"""
        stmt = (
            select(PublishScheduleLog)
            .where(
                PublishScheduleLog.schedule_id == schedule_id,
                PublishScheduleLog.status == "pending",
            )
        )
        logs = (await self.session.execute(stmt)).scalars().all()
        count = 0
        for log in logs:
            log.status = "cancelled"
            count += 1
        await self.session.commit()
        return count

    # ========== Scheduler 专用 ==========

    async def get_due_schedules(self) -> List[PublishSchedule]:
        """查询所有到期的启用规则（scheduler 调用）"""
        now = get_beijing_now()
        stmt = select(PublishSchedule).where(
            PublishSchedule.enabled == True,
            PublishSchedule.next_trigger_at != None,
            PublishSchedule.next_trigger_at <= now,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def advance_schedule(self, schedule: PublishSchedule) -> None:
        """触发后推进 next_trigger_at，once 模式完成后自动禁用"""
        schedule.last_triggered_at = get_beijing_now()

        if schedule.schedule_mode == "once":
            schedule.enabled = False
            schedule.next_trigger_at = None
        else:
            schedule.next_trigger_at = _compute_next_trigger(
                schedule.schedule_mode, schedule.schedule_config
            )

        await self.session.commit()
        await self.session.refresh(schedule)


# 计算函数导出供外部使用
__all__ = [
    "PublishScheduleService",
    "_compute_next_trigger",
]
