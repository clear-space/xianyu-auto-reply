"""
定时发布任务

功能：
1. 定期扫描到期的定时发布规则（xy_publish_schedules）
2. 调用 backend-web 的批量发布 API 触发发布
3. 记录执行日志并推进 next_trigger_at
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from loguru import logger

BEIJING_TZ = timezone(timedelta(hours=8))


# ==================== 时间计算工具（与 backend-web 的 publish_schedule_service 保持一致） ====================

def _parse_time(t_str: str) -> Optional[time]:
    try:
        h, m = t_str.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _random_time_between(start_str: str, end_str: str) -> time:
    start = _parse_time(start_str) or time(0, 0)
    end = _parse_time(end_str) or time(23, 59)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        end_minutes = start_minutes + 1
    random_minutes = random.randint(start_minutes, end_minutes)
    return time(random_minutes // 60, random_minutes % 60)


def _next_daily(config: dict, now: datetime, today) -> Optional[datetime]:
    use_random = config.get("random", False)
    time_range = config.get("time_range")
    times = config.get("times", [])

    if use_random and time_range:
        start_str = time_range.get("start", "00:00")
        end_str = time_range.get("end", "23:59")
        trigger_time = _random_time_between(start_str, end_str)
        candidate = datetime.combine(today, trigger_time).replace(tzinfo=BEIJING_TZ)
        if candidate <= now:
            candidate = datetime.combine(today + timedelta(days=1), _random_time_between(start_str, end_str)).replace(tzinfo=BEIJING_TZ)
        return candidate

    if times:
        time_points = [_parse_time(t) for t in times if _parse_time(t) is not None]
        time_points.sort()
        for tp in time_points:
            candidate = datetime.combine(today, tp).replace(tzinfo=BEIJING_TZ)
            if candidate > now:
                return candidate
        if time_points:
            return datetime.combine(today + timedelta(days=1), time_points[0]).replace(tzinfo=BEIJING_TZ)

    candidate = datetime.combine(today, time(0, 0)).replace(tzinfo=BEIJING_TZ)
    if candidate <= now:
        candidate = datetime.combine(today + timedelta(days=1), time(0, 0)).replace(tzinfo=BEIJING_TZ)
    return candidate


def _next_weekly(config: dict, now: datetime, today) -> Optional[datetime]:
    days = config.get("days", [])
    if not days:
        return None
    use_random = config.get("random", False)
    time_range = config.get("time_range")
    times = config.get("times", [])

    for offset in range(8):
        candidate_date = today + timedelta(days=offset)
        candidate_weekday = candidate_date.isoweekday()
        if candidate_weekday not in days:
            continue

        if offset == 0:
            if use_random and time_range:
                trigger_time = _random_time_between(time_range.get("start", "00:00"), time_range.get("end", "23:59"))
                candidate = datetime.combine(candidate_date, trigger_time).replace(tzinfo=BEIJING_TZ)
                if candidate > now:
                    return candidate
                continue
            if times:
                time_points = [_parse_time(t) for t in times if _parse_time(t) is not None]
                time_points.sort()
                for tp in time_points:
                    candidate = datetime.combine(candidate_date, tp).replace(tzinfo=BEIJING_TZ)
                    if candidate > now:
                        return candidate
                continue
            candidate = datetime.combine(candidate_date, time(0, 0)).replace(tzinfo=BEIJING_TZ)
            if candidate > now:
                return candidate
            continue

        # 未来某天，取最早时间点
        if use_random and time_range:
            trigger_time = _random_time_between(time_range.get("start", "00:00"), time_range.get("end", "23:59"))
        elif times:
            time_points = [_parse_time(t) for t in times if _parse_time(t) is not None]
            time_points.sort()
            trigger_time = time_points[0] if time_points else time(0, 0)
        else:
            trigger_time = time(0, 0)
        return datetime.combine(candidate_date, trigger_time).replace(tzinfo=BEIJING_TZ)

    return None


def _compute_next_trigger(schedule_mode: str, schedule_config: dict, after: datetime = None) -> Optional[datetime]:
    if after is None:
        now = datetime.now(BEIJING_TZ)
    else:
        now = after
    today = now.date()

    if schedule_mode == "once":
        dt_str = schedule_config.get("datetime")
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None
        # 确保时区一致
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TZ)
        if dt <= now:
            return None
        return dt

    if schedule_mode == "daily":
        return _next_daily(schedule_config, now, today)

    if schedule_mode == "weekly":
        return _next_weekly(schedule_config, now, today)

    return None


# ==================== 定时发布任务服务 ====================

class ScheduledPublishTaskService:
    """定时发布任务服务"""

    def __init__(self, task_name: str = "定时发布"):
        self.task_name = task_name
        self._lock = asyncio.Lock()

    async def execute(self):
        """扫描到期规则并触发批量发布"""
        if self._lock.locked():
            logger.info(f"【{self.task_name}】已有任务正在执行，跳过本次")
            return
        async with self._lock:
            await self._execute_inner()

    async def _execute_inner(self):
        from common.db.session import async_session_maker
        from common.models.publish_schedule import PublishSchedule
        from common.utils.time_utils import get_beijing_now
        from sqlalchemy import select

        logger.debug(f"【{self.task_name}】开始扫描到期规则")

        try:
            async with async_session_maker() as session:
                now = get_beijing_now()
                stmt = select(PublishSchedule).where(
                    PublishSchedule.enabled == True,
                    PublishSchedule.next_trigger_at != None,
                    PublishSchedule.next_trigger_at <= now,
                )
                due_schedules = list((await session.execute(stmt)).scalars().all())

                if not due_schedules:
                    logger.debug(f"【{self.task_name}】没有到期规则")
                    return

                logger.info(f"【{self.task_name}】发现 {len(due_schedules)} 条到期规则")

                for schedule in due_schedules:
                    logger.info(
                        f"【{self.task_name}】触发规则 #{schedule.id}「{schedule.name}」"
                        f", 账号={len(schedule.account_ids)}, 素材={len(schedule.material_ids)}"
                    )
                    try:
                        await self._trigger_schedule(session, schedule, now)
                    except Exception as e:
                        logger.error(f"【{self.task_name}】触发规则 #{schedule.id} 失败: {e}")
                        await session.rollback()

                    # 无论成功或失败都推进（_trigger_schedule 成功路径已自行 commit）
                    try:
                        await self._advance_schedule(session, schedule)
                    except Exception as e2:
                        logger.error(f"【{self.task_name}】推进规则 #{schedule.id} 失败: {e2}")
                        await session.rollback()

                    await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"【{self.task_name}】扫描异常: {e}")

    async def _trigger_schedule(self, session, schedule, now: datetime):
        """触发单条规则：创建执行日志 + 调用批量发布 API"""
        import uuid

        from app.core.http_client import get_http_client
        from app.core.config import get_settings
        from common.models.publish_schedule_log import PublishScheduleLog

        settings = get_settings()

        total_count = len(schedule.account_ids) * len(schedule.material_ids)

        log_entry = PublishScheduleLog(
            schedule_id=schedule.id,
            scheduled_at=now,
            total_count=total_count,
            status="running",
        )
        session.add(log_entry)
        await session.commit()
        await session.refresh(log_entry)

        batch_id = str(uuid.uuid4())
        log_entry.batch_id = batch_id
        await session.commit()

        backend_url = settings.backend_service_url or "http://localhost:8089"
        # 使用内部端点（无需用户认证，直接传入 user_id）
        publish_url = f"{backend_url.rstrip('/')}/api/v1/internal/publish/batch"

        client = get_http_client()
        try:
            # http_client.post() 返回已解析的 JSON dict
            result = await client.post(
                publish_url,
                json={
                    "user_id": schedule.user_id,
                    "account_ids": schedule.account_ids,
                    "material_ids": schedule.material_ids,
                    "schedule_log_id": log_entry.id,
                },
            )
            if result.get("success"):
                logger.info(
                    f"【{self.task_name}】规则 #{schedule.id} 批量发布已提交: batch_id={batch_id}"
                )
                log_entry.status = "running"
            else:
                log_entry.status = "failed"
                log_entry.error_message = result.get("message", "API返回失败")
                logger.warning(
                    f"【{self.task_name}】规则 #{schedule.id} 提交失败: "
                    f"{log_entry.error_message}"
                )
        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = f"调用API异常: {str(e)[:800]}"
            logger.error(f"【{self.task_name}】调用批量发布API异常: {e}")

        log_entry.executed_at = now
        await session.commit()

    async def _advance_schedule(self, session, schedule):
        """推进规则的 next_trigger_at，once 模式完成后自动禁用"""
        from common.utils.time_utils import get_beijing_now

        schedule.last_triggered_at = get_beijing_now()

        if schedule.schedule_mode == "once":
            schedule.enabled = False
            schedule.next_trigger_at = None
            logger.info(f"【{self.task_name}】单次规则 #{schedule.id} 执行完毕，已自动禁用")
        else:
            schedule.next_trigger_at = _compute_next_trigger(
                schedule.schedule_mode, schedule.schedule_config,
                after=get_beijing_now(),
            )
            logger.info(
                f"【{self.task_name}】规则 #{schedule.id} "
                f"下次触发: {schedule.next_trigger_at}"
            )

        await session.commit()


# 全局单例
scheduled_publish_task_service = ScheduledPublishTaskService(task_name="定时发布")
