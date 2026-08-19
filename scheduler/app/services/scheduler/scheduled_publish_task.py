"""
定时发布任务

功能：
1. 定期扫描到期的定时发布规则（xy_publish_schedules）
2. 调用 backend-web 的批量发布 API 触发发布
3. 记录执行日志并推进 next_trigger_at
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger

# 时间计算统一在 common.utils.schedule_time 维护（backend-web 与 scheduler 共用一份）
from common.utils.schedule_time import compute_next_trigger as _compute_next_trigger


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
        from common.models.publish_schedule_log import PublishScheduleLog
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
                    # 防重复触发：存在 running 执行记录的规则本轮跳过
                    running_stmt = (
                        select(PublishScheduleLog.id)
                        .where(
                            PublishScheduleLog.schedule_id == schedule.id,
                            PublishScheduleLog.status == "running",
                        )
                        .limit(1)
                    )
                    if (await session.execute(running_stmt)).scalars().first():
                        logger.info(
                            f"【{self.task_name}】规则 #{schedule.id}「{schedule.name}」"
                            f"存在执行中的记录，本轮跳过"
                        )
                        continue

                    logger.info(
                        f"【{self.task_name}】触发规则 #{schedule.id}「{schedule.name}」"
                        f", 账号={len(schedule.account_ids)}, 素材={len(schedule.material_ids)}"
                    )
                    try:
                        submitted = await self._trigger_schedule(session, schedule, now)
                    except Exception as e:
                        logger.error(f"【{self.task_name}】触发规则 #{schedule.id} 失败: {e}")
                        await session.rollback()
                        submitted = False

                    if submitted:
                        # 提交成功才推进调度周期
                        try:
                            await self._advance_schedule(session, schedule)
                        except Exception as e2:
                            logger.error(f"【{self.task_name}】推进规则 #{schedule.id} 失败: {e2}")
                            await session.rollback()
                    else:
                        # 提交失败：不推进 next_trigger_at（下轮扫描自动重试）；
                        # 连续 3 次失败才推进，避免无限重试占用扫描
                        recent_stmt = (
                            select(PublishScheduleLog.status)
                            .where(PublishScheduleLog.schedule_id == schedule.id)
                            .order_by(PublishScheduleLog.id.desc())
                            .limit(3)
                        )
                        recent_statuses = list(
                            (await session.execute(recent_stmt)).scalars().all()
                        )
                        consec_failed = 0
                        for st in recent_statuses:
                            if st == "failed":
                                consec_failed += 1
                            else:
                                break
                        if consec_failed >= 3:
                            logger.warning(
                                f"【{self.task_name}】规则 #{schedule.id} 连续 {consec_failed} 次"
                                f"提交失败，推进调度周期"
                            )
                            try:
                                await self._advance_schedule(session, schedule)
                            except Exception as e3:
                                logger.error(f"【{self.task_name}】推进规则 #{schedule.id} 失败: {e3}")
                                await session.rollback()

                    await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"【{self.task_name}】扫描异常: {e}")

    async def _trigger_schedule(self, session, schedule, now: datetime) -> bool:
        """触发单条规则：创建执行日志 + 调用内部批量发布 API（提交成功返回 True）"""
        import uuid

        from app.core.http_client import get_http_client
        from app.core.config import get_settings
        from common.models.publish_schedule_log import PublishScheduleLog
        from common.utils.time_utils import get_beijing_now

        settings = get_settings()

        batch_id = str(uuid.uuid4())

        # 计划时间记规则预计算的 next_trigger_at（而非扫描时间）
        log_entry = PublishScheduleLog(
            schedule_id=schedule.id,
            schedule_name=schedule.name,
            scheduled_at=schedule.next_trigger_at or now,
            total_count=len(schedule.account_ids) * len(schedule.material_ids),
            status="running",
            batch_id=batch_id,
        )
        session.add(log_entry)
        await session.commit()
        await session.refresh(log_entry)

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
                    "schedule_id": schedule.id,
                    "schedule_log_id": log_entry.id,
                    "batch_id": batch_id,
                },
            )
            if result.get("success"):
                logger.info(
                    f"【{self.task_name}】规则 #{schedule.id} 批量发布已提交: batch_id={batch_id}"
                )
                return True
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

        log_entry.executed_at = get_beijing_now()
        await session.commit()
        return False

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
                force_next_day=True,  # 触发后推进：随机模式固定到明天，防止同窗口重复触发
            )
            logger.info(
                f"【{self.task_name}】规则 #{schedule.id} "
                f"下次触发: {schedule.next_trigger_at}"
            )

        await session.commit()


# 全局单例
scheduled_publish_task_service = ScheduledPublishTaskService(task_name="定时发布")
