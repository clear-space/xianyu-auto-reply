"""
商品指标每日快照定时任务

功能：
1. 每日凌晨 3:00~4:00 窗口内随机时刻执行一次，采集各账号在售商品的
   曝光/浏览/咨询/成交（当日+近7天窗口）与累计想要数，写入 xy_item_stats_daily
2. 每次执行顺带清理超过保留天数的过期快照（系统设置 item_stats.retention_days，默认 30 天）
3. 启动补跑：scheduler 启动时若当日快照不存在则立即执行一次，保证上线当天即有数据

调度设计：
- 调度循环每 10 分钟调用一次 execute()，execute 内部做窗口与随机延迟判断
- 首次进入 3:00~4:00 窗口时抽取随机延迟 0~60 分钟（截断到 03:59），到点执行
- 每天只执行一次：_ran_date 记录当日已执行（进程内）；跨重启靠 has_today_snapshot 幂等保护
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from common.services.item_stats_service import (
    cleanup_expired_snapshots,
    has_today_snapshot,
    snapshot_account_stats,
)
from common.utils.time_utils import get_beijing_now


class ItemStatsSnapshotTaskService:
    """商品指标每日快照任务服务"""

    # 执行窗口（北京时间，含起点不含终点）
    WINDOW_START_HOUR = 3
    WINDOW_END_HOUR = 4
    # 窗口内随机延迟上限（分钟），实际不超过窗口剩余时长
    MAX_RANDOM_DELAY_MINUTES = 60

    def __init__(self):
        self.task_name = "商品指标快照"
        # 执行锁：避免定时循环与启动补跑并发执行
        self._lock = asyncio.Lock()
        # 当日已执行标记（进程内）
        self._ran_date: Optional[str] = None
        # 窗口内随机目标执行时刻（首次进入窗口时抽取）
        self._pending_target: Optional[datetime] = None

    def _today_str(self) -> str:
        return get_beijing_now().strftime("%Y%m%d")

    def _in_window(self, now: datetime) -> bool:
        return self.WINDOW_START_HOUR <= now.hour < self.WINDOW_END_HOUR

    async def execute(self) -> Optional[str]:
        """调度循环每 10 分钟调用一次：窗口 + 随机延迟判断，返回状态说明（None 表示已执行）"""
        now = get_beijing_now()
        today = now.strftime("%Y%m%d")

        if not self._in_window(now):
            # 离开窗口：清理待执行目标，重置当日标记以迎接新的一天
            self._pending_target = None
            return "not_in_window"

        if self._ran_date == today:
            return "already_run_today"

        # 首次进入窗口：抽取随机执行时刻
        if self._pending_target is None:
            max_delay = min(self.MAX_RANDOM_DELAY_MINUTES, 60 - now.minute)
            delay = random.randint(0, max(0, max_delay))
            self._pending_target = now + timedelta(minutes=delay)
            logger.info(
                f"【{self.task_name}】进入执行窗口，随机延迟 {delay} 分钟，"
                f"目标时刻 {self._pending_target.strftime('%H:%M')}"
            )
            return "waiting_random_delay"

        if now < self._pending_target:
            return "waiting_random_delay"

        # 到点执行
        self._pending_target = None
        self._ran_date = today
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有执行进行中，跳过本次触发")
            return "locked"
        async with self._lock:
            await self._run()
        return None

    async def startup_catchup(self) -> None:
        """启动补跑：当日尚无快照时立即执行一次（不判断窗口），保证上线当天即有数据"""
        if self._lock.locked():
            return
        async with self._lock:
            try:
                today = self._today_str()
                async with async_session_maker() as session:
                    result = await session.execute(
                        select(XYAccount).where(
                            XYAccount.status == "active",
                            XYAccount.cookie.isnot(None),
                            XYAccount.cookie != "",
                        )
                    )
                    accounts = list(result.scalars().all())
                    # 任一账号当日已有快照则视为已补跑（避免多次重启重复采集）
                    for account in accounts:
                        if await has_today_snapshot(session, account.account_id, today):
                            logger.info(
                                f"【{self.task_name}】当日快照已存在（账号 {account.account_id}），跳过启动补跑"
                            )
                            self._ran_date = today
                            return
                logger.info(f"【{self.task_name}】启动补跑：当日尚无快照，立即执行一次")
                await self._run()
                self._ran_date = self._today_str()
            except Exception as e:
                logger.error(f"【{self.task_name}】启动补跑异常: {e}")

    async def _run(self) -> None:
        """实际执行：遍历活跃账号采集快照 + 清理过期数据"""
        logger.info(f"【{self.task_name}】开始执行")
        stat_date = self._today_str()

        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(XYAccount).where(
                        XYAccount.status == "active",
                        XYAccount.cookie.isnot(None),
                        XYAccount.cookie != "",
                    )
                )
                accounts = list(result.scalars().all())
        except Exception as e:
            logger.error(f"【{self.task_name}】查询活跃账号失败: {e}")
            return

        if not accounts:
            logger.warning(f"【{self.task_name}】无可用活跃账号，跳过本次执行")
            return

        ok_count = 0
        for account in accounts:
            try:
                async with async_session_maker() as session:
                    if await has_today_snapshot(session, account.account_id, stat_date):
                        logger.info(f"【{self.task_name}】账号 {account.account_id} 当日已采集，跳过")
                        continue
                    result_info = await snapshot_account_stats(session, account, stat_date)
                    if result_info.get("success"):
                        ok_count += 1
                    else:
                        logger.warning(
                            f"【{self.task_name}】账号 {account.account_id} 采集失败: {result_info.get('error')}"
                        )
            except Exception as e:
                logger.error(f"【{self.task_name}】账号 {account.account_id} 采集异常: {e}")

        # 清理过期快照（无论采集结果如何都执行，防止清理被采集失败卡住）
        try:
            async with async_session_maker() as session:
                await cleanup_expired_snapshots(session)
        except Exception as e:
            logger.error(f"【{self.task_name}】清理过期快照异常: {e}")

        logger.info(f"【{self.task_name}】执行完成：成功 {ok_count}/{len(accounts)} 个账号")


# 全局实例
item_stats_snapshot_task_service = ItemStatsSnapshotTaskService()
