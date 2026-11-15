"""
定时发布任务

功能：
1. 定期扫描到期的定时发布规则（xy_publish_schedules）
2. 调用 backend-web 的批量发布 API 触发发布
3. 记录执行日志并推进 next_trigger_at
4. random+time_range 模式的规则：窗口锚点到期后为每个账号独立生成窗口内随机触发时间
   （trigger_state），逐账号到点触发（每账号一条执行记录），全部账号触发后推进下一窗口
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from loguru import logger

# 时间计算统一在 common.utils.schedule_time 维护（backend-web 与 scheduler 共用一份）
from common.utils.schedule_time import compute_publish_window_anchor as _compute_window_anchor
from common.utils.schedule_time import random_time_between
from common.utils.time_utils import BEIJING_TZ


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

    @staticmethod
    def _uses_per_account_trigger(schedule) -> bool:
        """判断规则是否走账号级独立触发（仅 random+time_range 的 daily/weekly 规则）"""
        cfg = schedule.schedule_config or {}
        return (
            schedule.schedule_mode in ("daily", "weekly")
            and bool(cfg.get("random"))
            and bool(cfg.get("time_range"))
        )

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
                    if self._uses_per_account_trigger(schedule):
                        # 账号级独立触发分支（random+time_range）
                        try:
                            await self._execute_account_triggers(session, schedule, now)
                        except Exception as e:
                            logger.error(
                                f"【{self.task_name}】规则 #{schedule.id} 账号级触发异常: {e}"
                            )
                            await session.rollback()
                    else:
                        # 规则级触发分支（once/固定时间点，行为不变）
                        try:
                            await self._execute_rule_trigger(session, schedule, now)
                        except Exception as e:
                            logger.error(f"【{self.task_name}】触发规则 #{schedule.id} 失败: {e}")
                            await session.rollback()

                    await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"【{self.task_name}】扫描异常: {e}")

    async def _execute_rule_trigger(self, session, schedule, now: datetime) -> None:
        """规则级触发（once/固定时间点模式）：与原逻辑一致，单条规则整体触发"""
        from common.models.publish_schedule_log import PublishScheduleLog
        from sqlalchemy import select

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
            return

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

    async def _execute_account_triggers(self, session, schedule, now: datetime) -> None:
        """账号级独立触发（random+time_range 模式）

        1. 物化：窗口锚点到期且本窗口触发计划未生成时，为每个账号独立随机生成窗口内触发时间
        2. 逐账号触发：trigger_at <= now 且 pending 的账号，逐条创建执行记录并调用内部 API
        3. 推进：全部账号已触发（fired）后，推进下一窗口锚点并清空计划
        """
        from common.models.publish_schedule_log import PublishScheduleLog
        from common.utils.time_utils import get_beijing_now
        from sqlalchemy import select

        # ---------- 1. 物化账号触发计划 ----------
        anchor_date = schedule.next_trigger_at.date()
        window_date_str = anchor_date.isoformat()
        ts = schedule.trigger_state or {}
        if ts.get("window_date") != window_date_str:
            cfg = schedule.schedule_config or {}
            tr = cfg.get("time_range") or {}
            plan = {}
            for aid in list(dict.fromkeys(schedule.account_ids or [])):
                rt = random_time_between(
                    str(tr.get("start", "00:00")), str(tr.get("end", "23:59"))
                )
                trigger_at = datetime.combine(anchor_date, rt).replace(tzinfo=BEIJING_TZ)
                plan[aid] = {"trigger_at": trigger_at.isoformat(), "status": "pending"}
            ts = {"window_date": window_date_str, "accounts": plan}
            schedule.trigger_state = ts
            await session.commit()
            logger.info(
                f"【{self.task_name}】规则 #{schedule.id} 已生成账号级触发计划"
                f"（窗口 {window_date_str}，{len(plan)} 个账号）"
            )

        # ---------- 2. 逐账号触发 ----------
        accounts = ts.get("accounts") or {}
        for aid in list(accounts.keys()):
            info = accounts.get(aid)
            if not info or info.get("status") != "pending":
                continue
            try:
                trigger_at = datetime.fromisoformat(str(info["trigger_at"]))
                if trigger_at.tzinfo is None:
                    trigger_at = trigger_at.replace(tzinfo=BEIJING_TZ)
            except ValueError:
                trigger_at = now
            if trigger_at > now:
                continue

            # 防重复：该账号存在 running 执行记录则跳过
            running_stmt = (
                select(PublishScheduleLog.id)
                .where(
                    PublishScheduleLog.schedule_id == schedule.id,
                    PublishScheduleLog.account_id == aid,
                    PublishScheduleLog.status == "running",
                )
                .limit(1)
            )
            if (await session.execute(running_stmt)).scalars().first():
                continue

            try:
                submitted = await self._trigger_account(
                    session, schedule, aid, trigger_at
                )
            except Exception as e:
                logger.error(
                    f"【{self.task_name}】规则 #{schedule.id} 账号 {aid} 触发失败: {e}"
                )
                await session.rollback()
                submitted = False

            if submitted:
                # 先拷贝再改：ts 与 schedule.trigger_state 是同一对象，原地修改会把
                # "旧值"一并改掉，SQLAlchemy 判定无变更而不发 UPDATE
                new_ts = copy.deepcopy(ts)
                new_ts["accounts"][aid]["status"] = "fired"
                schedule.trigger_state = new_ts
                ts = new_ts
            else:
                # 提交失败：保持 pending 下轮重试；连续 3 次失败本窗口放弃，避免无限重试
                recent_stmt = (
                    select(PublishScheduleLog.status)
                    .where(
                        PublishScheduleLog.schedule_id == schedule.id,
                        PublishScheduleLog.account_id == aid,
                    )
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
                        f"【{self.task_name}】规则 #{schedule.id} 账号 {aid} "
                        f"连续 {consec_failed} 次提交失败，本窗口放弃"
                    )
                    new_ts = copy.deepcopy(ts)
                    new_ts["accounts"][aid]["status"] = "fired"
                    schedule.trigger_state = new_ts
                    ts = new_ts

            await session.commit()
            await asyncio.sleep(2)

        # ---------- 3. 全部触发后推进下一窗口 ----------
        # 用更新后的 ts（fire 循环内先拷贝再改）判断，不能用循环前的 accounts 旧引用
        accounts_now = (ts or {}).get("accounts") or {}
        if accounts_now and all(a.get("status") == "fired" for a in accounts_now.values()):
            schedule.last_triggered_at = get_beijing_now()
            schedule.next_trigger_at = _compute_window_anchor(
                schedule.schedule_mode, schedule.schedule_config,
                after=get_beijing_now(),
                force_next_day=True,
            )
            schedule.trigger_state = None
            await session.commit()
            logger.info(
                f"【{self.task_name}】规则 #{schedule.id} 全部账号已触发，"
                f"推进下一窗口: {schedule.next_trigger_at}"
            )

    async def _trigger_account(
        self, session, schedule, account_id: str, trigger_at: datetime
    ) -> bool:
        """触发规则内单个账号：创建账号级执行记录 + 调用内部批量发布 API"""
        import uuid

        from app.core.http_client import get_http_client
        from app.core.config import get_settings
        from common.models.publish_schedule_log import PublishScheduleLog
        from common.utils.time_utils import get_beijing_now

        settings = get_settings()

        batch_id = str(uuid.uuid4())

        log_entry = PublishScheduleLog(
            schedule_id=schedule.id,
            schedule_name=schedule.name,
            account_id=account_id,
            scheduled_at=trigger_at,
            total_count=0,
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
                    "account_ids": [account_id],
                    "material_ids": schedule.material_ids,
                    "schedule_id": schedule.id,
                    "schedule_log_id": log_entry.id,
                    "batch_id": batch_id,
                },
            )
            if result.get("success"):
                logger.info(
                    f"【{self.task_name}】规则 #{schedule.id} 账号 {account_id} "
                    f"发布已提交: batch_id={batch_id}"
                )
                return True
            log_entry.status = "failed"
            log_entry.error_message = result.get("message", "API返回失败")
            logger.warning(
                f"【{self.task_name}】规则 #{schedule.id} 账号 {account_id} 提交失败: "
                f"{log_entry.error_message}"
            )
        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = f"调用API异常: {str(e)[:800]}"
            logger.error(
                f"【{self.task_name}】规则 #{schedule.id} 账号 {account_id} "
                f"调用批量发布API异常: {e}"
            )

        log_entry.executed_at = get_beijing_now()
        await session.commit()
        return False

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
            schedule.next_trigger_at = _compute_window_anchor(
                schedule.schedule_mode, schedule.schedule_config,
                after=get_beijing_now(),
                force_next_day=True,  # 触发后推进：随机模式固定到明天，防止同窗口重复触发
            )
            schedule.trigger_state = None
            logger.info(
                f"【{self.task_name}】规则 #{schedule.id} "
                f"下次触发: {schedule.next_trigger_at}"
            )

        await session.commit()


# 全局单例
scheduled_publish_task_service = ScheduledPublishTaskService(task_name="定时发布")
