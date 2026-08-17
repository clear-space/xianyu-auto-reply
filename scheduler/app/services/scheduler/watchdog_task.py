"""
调度器自愈监测任务

功能：
1. 自愈：检查各任务循环是否意外退出，意外退出自动重启循环
2. 迟到告警：检查到期超过宽限期（30 分钟）仍无执行记录的发布/下架规则，
   写告警日志并通过系统通知渠道推送（不自动补触发，避免副作用）
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Set

from loguru import logger


class WatchdogTaskService:
    """调度器自愈监测任务"""

    # 到期未执行的宽限期（分钟）
    GRACE_MINUTES = 30
    # 告警去重集合上限（防止异常场景下无界增长）
    _MAX_ALERTED = 1000

    def __init__(self, task_name: str = "调度器自愈监测"):
        self.task_name = task_name
        self._lock = asyncio.Lock()
        # 已告警窗口去重：(类型, 规则ID, next_trigger_at)
        self._alerted: Set[tuple] = set()

    async def execute(self):
        """执行监测：自愈检查 + 迟到告警"""
        if self._lock.locked():
            logger.info(f"【{self.task_name}】已有任务正在执行，跳过本次")
            return
        async with self._lock:
            await self._check_loops()
            await self._check_late_schedules()

    # ==================== 自愈：循环意外退出自动重启 ====================

    async def _check_loops(self):
        from app.services.scheduler_service import get_scheduler_service

        scheduler = get_scheduler_service()
        try:
            restarted = scheduler.check_and_restart_loops()
        except Exception as exc:
            logger.error(f"【{self.task_name}】自愈检查异常: {exc}")
            return
        if restarted:
            logger.warning(f"【{self.task_name}】已自动重启意外退出的任务循环: {restarted}")

    # ==================== 迟到告警：到期未执行规则 ====================

    async def _check_late_schedules(self):
        from sqlalchemy import func, select

        from common.db.session import async_session_maker
        from common.models.offline_schedule import OfflineSchedule
        from common.models.offline_schedule_log import OfflineScheduleLog
        from common.models.publish_schedule import PublishSchedule
        from common.models.publish_schedule_log import PublishScheduleLog
        from common.utils.time_utils import get_beijing_now

        now = get_beijing_now()
        cutoff = now - timedelta(minutes=self.GRACE_MINUTES)
        late_msgs: List[str] = []

        try:
            async with async_session_maker() as session:
                # 定时发布规则
                pub_rows = list(
                    (
                        await session.execute(
                            select(PublishSchedule).where(
                                PublishSchedule.enabled == True,
                                PublishSchedule.next_trigger_at != None,
                                PublishSchedule.next_trigger_at <= cutoff,
                            )
                        )
                    ).scalars().all()
                )
                for s in pub_rows:
                    alert_key = ("publish", s.id, s.next_trigger_at)
                    if alert_key in self._alerted:
                        continue
                    cnt = (
                        await session.execute(
                            select(func.count())
                            .select_from(PublishScheduleLog)
                            .where(
                                PublishScheduleLog.schedule_id == s.id,
                                PublishScheduleLog.scheduled_at >= s.next_trigger_at,
                            )
                        )
                    ).scalar() or 0
                    if cnt == 0:
                        self._record_alert(alert_key)
                        late_msgs.append(
                            f"定时发布规则 #{s.id}「{s.name}」已到期超过 {self.GRACE_MINUTES} 分钟未执行"
                        )

                # 自动下架规则
                off_rows = list(
                    (
                        await session.execute(
                            select(OfflineSchedule).where(
                                OfflineSchedule.enabled == True,
                                OfflineSchedule.next_trigger_at != None,
                                OfflineSchedule.next_trigger_at <= cutoff,
                            )
                        )
                    ).scalars().all()
                )
                for s in off_rows:
                    alert_key = ("offline", s.id, s.next_trigger_at)
                    if alert_key in self._alerted:
                        continue
                    cnt = (
                        await session.execute(
                            select(func.count())
                            .select_from(OfflineScheduleLog)
                            .where(
                                OfflineScheduleLog.schedule_id == s.id,
                                OfflineScheduleLog.scheduled_at >= s.next_trigger_at,
                            )
                        )
                    ).scalar() or 0
                    if cnt == 0:
                        self._record_alert(alert_key)
                        late_msgs.append(
                            f"自动下架规则 #{s.id}「{s.name}」已到期超过 {self.GRACE_MINUTES} 分钟未执行"
                        )
        except Exception as exc:
            logger.error(f"【{self.task_name}】迟到检查异常: {exc}")
            return

        for msg in late_msgs:
            logger.warning(f"【{self.task_name}】{msg}")
        if late_msgs:
            await self._send_alert(late_msgs)

    def _record_alert(self, key: tuple) -> None:
        """记录已告警窗口（去重，防止每个监测周期重复推送）"""
        self._alerted.add(key)
        if len(self._alerted) > self._MAX_ALERTED:
            # 简单防膨胀：清空重新累计
            self._alerted.clear()

    async def _send_alert(self, late_msgs: List[str]) -> None:
        """通过系统通知渠道推送告警（无渠道配置时仅留日志）"""
        from sqlalchemy import select

        from common.db.session import async_session_maker
        from common.models.notification_channel import NotificationChannel

        content = "【调度器监测告警】\n" + "\n".join(late_msgs)

        channels: List[NotificationChannel] = []
        try:
            async with async_session_maker() as session:
                channels = list(
                    (
                        await session.execute(
                            select(NotificationChannel).where(
                                NotificationChannel.enabled == True
                            )
                        )
                    ).scalars().all()
                )
        except Exception as exc:
            logger.error(f"【{self.task_name}】加载通知渠道失败: {exc}")
            return

        if not channels:
            logger.info(f"【{self.task_name}】未配置启用中的通知渠道，告警仅记录日志")
            return

        from common.utils.notification_utils import (
            parse_notification_config,
            send_bark_notification,
            send_dingtalk_notification,
            send_email_notification,
            send_feishu_notification,
            send_pushplus_notification,
            send_telegram_notification,
            send_webhook_notification,
            send_wechat_notification,
        )

        for channel in channels:
            channel_type = channel.channel_type or ""
            try:
                config_data = parse_notification_config(channel.config_payload or {})
                if channel_type in ("dingtalk", "ding_talk"):
                    await send_dingtalk_notification(config_data, content)
                elif channel_type in ("feishu", "lark"):
                    await send_feishu_notification(config_data, content)
                elif channel_type == "bark":
                    await send_bark_notification(config_data, content)
                elif channel_type == "email":
                    await send_email_notification(config_data, content)
                elif channel_type == "webhook":
                    await send_webhook_notification(config_data, content)
                elif channel_type in ("wechat", "wechat_work"):
                    await send_wechat_notification(config_data, content)
                elif channel_type == "telegram":
                    await send_telegram_notification(config_data, content)
                elif channel_type == "pushplus":
                    await send_pushplus_notification(config_data, content)
                else:
                    logger.warning(f"【{self.task_name}】不支持的通知渠道类型: {channel_type}")
            except Exception as exc:
                logger.error(f"【{self.task_name}】渠道 {channel_type} 发送告警失败: {exc}")


# 全局单例
watchdog_task_service = WatchdogTaskService(task_name="调度器自愈监测")
