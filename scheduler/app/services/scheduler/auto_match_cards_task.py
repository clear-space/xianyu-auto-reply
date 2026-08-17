"""
自动关联卡券任务

功能：
1. 定期扫描（默认600秒）：为至少有一个账号开启 auto_match_cards 的用户执行一键关联
2. 复用 CardMatcher.match_cards_by_prefix_number（与商品管理一键关联同一实现，幂等、已存在跳过）
3. 兜底入库钩子未覆盖的场景：新建卡券、钩子执行失败、存量数据
"""
from __future__ import annotations

import asyncio

from loguru import logger


class AutoMatchCardsTaskService:
    """自动关联卡券任务服务"""

    def __init__(self, task_name: str = "自动关联卡券"):
        self.task_name = task_name
        self._lock = asyncio.Lock()

    async def execute(self):
        """扫描并补齐缺失的卡券关联"""
        if self._lock.locked():
            logger.info(f"【{self.task_name}】已有任务正在执行，跳过本次")
            return
        async with self._lock:
            await self._execute_inner()

    async def _execute_inner(self):
        from sqlalchemy import select

        from common.db.session import async_session_maker
        from common.models.xy_account import XYAccount
        from common.services.card_matcher import CardMatcher

        # 1. 找出至少有一个账号开启自动关联开关的用户（尊重账号级开关）
        async with async_session_maker() as session:
            rows = (
                await session.execute(
                    select(XYAccount.owner_id)
                    .where(XYAccount.auto_match_cards == True)
                    .distinct()
                )
            ).all()
            user_ids = [r[0] for r in rows if r[0] is not None]

        if not user_ids:
            logger.debug(f"【{self.task_name}】没有开启自动关联的账号，跳过")
            return

        # 2. 逐用户执行一键关联（幂等，每用户独立 session，失败不互相影响）
        total_added = 0
        for user_id in user_ids:
            try:
                async with async_session_maker() as session:
                    matcher = CardMatcher(session)
                    stats = await matcher.match_cards_by_prefix_number(user_id)
                    added = int(stats.get("added") or 0)
                    total_added += added
                    if added:
                        logger.info(
                            f"【{self.task_name}】用户 {user_id} 新增关联 {added} 对"
                            f"（匹配卡券 {stats.get('matched_cards')} 张）"
                        )
            except Exception as exc:
                logger.warning(f"【{self.task_name}】用户 {user_id} 关联失败: {exc}")

        if total_added:
            logger.info(f"【{self.task_name}】本轮完成，共新增 {total_added} 对关联")


# 全局单例
auto_match_cards_task_service = AutoMatchCardsTaskService(task_name="自动关联卡券")
