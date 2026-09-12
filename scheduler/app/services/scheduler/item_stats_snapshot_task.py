"""
商品指标每日快照定时任务

功能：
1. 每日凌晨 3:00~4:00 窗口内随机时刻执行一次，采集各账号在售商品的
   曝光/浏览/咨询/成交（当日+近7天窗口）与累计想要数，写入 xy_item_stats_daily
2. 每次执行顺带清理超过保留天数的过期快照（系统设置 item_stats.retention_days，默认 30 天）
3. 启动补跑：scheduler 启动时按账号粒度补齐当日快照，保证上线当天即有数据

调度设计（2026-09 修复漏采缺陷后）：
- 调度循环每 interval 秒调用一次 execute()（间隔由 scheduler_service 钳制 ≤600s，
  防止管理员把间隔调大后轮询网格整天错过采集窗口）
- 首次进入 3:00~4:00 窗口时抽取秒级随机延迟，目标时刻严格落在窗口内（< 04:00:00），
  窗口内到点执行
- 兜底执行：若轮询网格错过目标时刻或整个窗口（进程卡顿/间隔被调大），
  在 4:00 之后的首个 tick 立即执行，保证"每天必跑一次"
- 失败重试：当日采集存在失败账号时，后续 tick 自动重试（最多 MAX_RETRIES 次，
  超过 RETRY_DEADLINE_HOUR 放弃）；_ran_date 仅在全部成功或放弃重试后置位
- 每天只执行一次（成功后）；跨重启靠 has_today_snapshot 逐账号幂等保护

历史缺陷记录（修复前）：旧实现 max_delay = min(60, 60 - now.minute) 未做秒级截断，
目标时刻可落在 04:00:00 之后；且 execute() 先判"是否在窗口内"再判"是否到点"，
目标时刻越过窗口后 _pending_target 被静默清空 → 仿真实测约 9% 的天数整日漏采。
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

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
    # 窗口内随机延迟上限（分钟），实际以"目标严格落在窗口内"为准
    MAX_RANDOM_DELAY_MINUTES = 60
    # 采集失败后的自动重试次数上限与当日重试截止小时
    MAX_RETRIES = 2
    RETRY_DEADLINE_HOUR = 12

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self.task_name = "商品指标快照"
        # 执行锁：避免定时循环与启动补跑并发执行
        self._lock = asyncio.Lock()
        # 当日已执行标记（进程内；仅在全部成功或放弃重试后置位）
        self._ran_date: Optional[str] = None
        # 窗口内随机目标执行时刻（首次进入窗口时抽取）
        self._pending_target: Optional[datetime] = None
        # 当日失败重试计数（全部成功后清零）
        self._retry_count: int = 0
        # 时钟注入点（测试用假时钟；默认北京当前时间）
        self._now_fn: Callable[[], datetime] = now_fn or get_beijing_now

    def _now(self) -> datetime:
        return self._now_fn()

    def _today_str(self) -> str:
        return self._now().strftime("%Y%m%d")

    def _in_window(self, now: datetime) -> bool:
        return self.WINDOW_START_HOUR <= now.hour < self.WINDOW_END_HOUR

    async def execute(self) -> Optional[str]:
        """调度循环周期性调用：窗口判断 + 随机延迟 + 漏采兜底 + 失败重试

        返回状态说明（None 表示本次已执行），仅供日志/排查。
        """
        now = self._now()
        today = now.strftime("%Y%m%d")

        if self._ran_date == today:
            self._pending_target = None
            return "already_run_today"

        if self._in_window(now):
            if self._pending_target is None:
                # 首次进入窗口：抽取秒级随机延迟，目标严格落在窗口内（< 04:00:00）。
                # 旧实现按整分钟计算且未截断，目标可落到 04:00:00 之后导致当日漏采。
                window_end = now.replace(
                    hour=self.WINDOW_END_HOUR, minute=0, second=0, microsecond=0
                )
                max_delay_sec = min(
                    self.MAX_RANDOM_DELAY_MINUTES * 60,
                    max(0, int((window_end - now).total_seconds()) - 1),
                )
                delay = random.randint(0, max_delay_sec)
                self._pending_target = now + timedelta(seconds=delay)
                logger.info(
                    f"【{self.task_name}】进入执行窗口，随机延迟 {delay} 秒，"
                    f"目标时刻 {self._pending_target.strftime('%H:%M:%S')}"
                )
            if now < self._pending_target:
                return "waiting_random_delay"
            # 到点执行（含 delay=0 立即执行）
            self._pending_target = None
            return await self._run_guarded(now, today)

        # 窗口外
        if now.hour >= self.WINDOW_END_HOUR:
            # 兜底：目标时刻被轮询错过 / 整个窗口被错过 / 上次有失败账号待重试。
            # 目标时刻恒 < 04:00:00，窗口外且未完成当日采集即进入此分支；
            # 0:00~2:59 的 tick 不会走到这里（hour < 4），不会提前执行。
            self._pending_target = None
            return await self._run_guarded(now, today)

        self._pending_target = None
        return "not_in_window"

    async def _run_guarded(self, now: datetime, today: str) -> Optional[str]:
        """加锁执行一次采集并做当日完成/重试记账"""
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有执行进行中，跳过本次触发")
            return "locked"
        async with self._lock:
            try:
                result = await self._run()
            except Exception as e:
                logger.error(f"【{self.task_name}】执行异常: {e}")
                result = {"ok": 0, "failed": 1}
        if result.get("failed") == 0:
            self._ran_date = today
            self._retry_count = 0
        else:
            self._retry_count += 1
            if self._retry_count > self.MAX_RETRIES or now.hour >= self.RETRY_DEADLINE_HOUR:
                logger.warning(
                    f"【{self.task_name}】重试 {self._retry_count - 1} 次后仍有 "
                    f"{result.get('failed')} 个账号失败，今日不再重试"
                )
                self._ran_date = today
            else:
                logger.info(
                    f"【{self.task_name}】{result.get('failed')} 个账号采集失败，"
                    f"将在下一轮自动重试（第 {self._retry_count}/{self.MAX_RETRIES} 次）"
                )
        return None

    async def run_now(self) -> None:
        """手动触发：跳过窗口/随机延迟/当日已跑判断，强制重采当日快照（UPSERT 覆盖当天数据）

        手动执行后标记当日完成，避免 4:00 后的兜底逻辑再触发一次重复采集。
        """
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有执行进行中，跳过手动触发")
            return
        async with self._lock:
            try:
                await self._run(force=True)
            except Exception as e:
                logger.error(f"【{self.task_name}】手动触发执行异常: {e}")
            self._pending_target = None
            self._retry_count = 0
            self._ran_date = self._today_str()

    async def startup_catchup(self) -> None:
        """启动补跑：按账号粒度补齐当日快照，保证上线/重启当天即有数据

        直接执行 _run（其内部已按账号逐条 has_today_snapshot 判断），
        未全部成功时保留 _ran_date 空位，由调度循环继续自动重试。
        """
        if self._lock.locked():
            return
        async with self._lock:
            try:
                today = self._today_str()
                result = await self._run()
                self._pending_target = None
                if result.get("failed") == 0:
                    self._ran_date = today
                    self._retry_count = 0
                else:
                    self._retry_count = 1
                    logger.warning(
                        f"【{self.task_name}】启动补跑有 {result.get('failed')} 个账号失败，"
                        f"将由调度循环自动重试"
                    )
            except Exception as e:
                logger.error(f"【{self.task_name}】启动补跑异常: {e}")

    async def _run(self, force: bool = False) -> Dict[str, int]:
        """实际执行：遍历活跃账号采集快照 + 清理过期数据

        Args:
            force: True=手动触发，跳过「当日已采集」判断强制重采（UPSERT 覆盖当天数据）

        Returns:
            {"ok": 成功账号数, "failed": 失败账号数, "write_failed": 商品级快照写入失败数,
             "want_failed": 想要数抓取失败数}（当日已有快照被跳过的账号不计入）
        """
        logger.info(f"【{self.task_name}】开始执行（force={force}）")
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
            return {"ok": 0, "failed": 1}

        if not accounts:
            logger.warning(f"【{self.task_name}】无可用活跃账号，跳过本次执行")
            return {"ok": 0, "failed": 0}

        ok_count = 0
        fail_count = 0
        write_failed_total = 0
        want_failed_total = 0
        for account in accounts:
            try:
                async with async_session_maker() as session:
                    if not force and await has_today_snapshot(session, account.account_id, stat_date):
                        logger.info(f"【{self.task_name}】账号 {account.account_id} 当日已采集，跳过")
                        continue
                    result_info = await snapshot_account_stats(session, account, stat_date)
                    write_failed_total += int(result_info.get("write_failed") or 0)
                    want_failed_total += int(result_info.get("want_failed") or 0)
                    if result_info.get("success"):
                        ok_count += 1
                    else:
                        fail_count += 1
                        logger.warning(
                            f"【{self.task_name}】账号 {account.account_id} 采集失败: {result_info.get('error')}"
                        )
            except Exception as e:
                fail_count += 1
                logger.error(f"【{self.task_name}】账号 {account.account_id} 采集异常: {e}")

        # 清理过期快照（无论采集结果如何都执行，防止清理被采集失败卡住）
        try:
            async with async_session_maker() as session:
                await cleanup_expired_snapshots(session)
        except Exception as e:
            logger.error(f"【{self.task_name}】清理过期快照异常: {e}")

        logger.info(
            f"【{self.task_name}】执行完成：成功 {ok_count} 个账号，失败 {fail_count} 个账号"
            f"（商品级：快照写入失败 {write_failed_total} 件、想要数抓取失败 {want_failed_total} 件）"
        )
        return {"ok": ok_count, "failed": fail_count,
                "write_failed": write_failed_total, "want_failed": want_failed_total}


# 全局实例
item_stats_snapshot_task_service = ItemStatsSnapshotTaskService()
