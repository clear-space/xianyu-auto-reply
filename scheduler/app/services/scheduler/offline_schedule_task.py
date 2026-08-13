"""
自动下架任务

功能：
1. 定期扫描到期的自动下架规则
2. 筛选「上架超过 X 天 + 最近 Y 天无订单」的商品
3. 调用闲鱼批量下架 API + 删除数据库记录
4. 推进 next_trigger_at
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from loguru import logger


class OfflineScheduleTaskService:
    """自动下架定时任务服务"""

    def __init__(self, task_name: str = "自动下架"):
        self.task_name = task_name
        self._lock = asyncio.Lock()

    async def execute(self):
        if self._lock.locked():
            logger.info(f"【{self.task_name}】已有任务正在执行，跳过本次")
            return
        async with self._lock:
            await self._execute_inner()

    async def _execute_inner(self):
        from common.db.session import async_session_maker
        from common.models.offline_schedule import OfflineSchedule
        from common.models.xy_catalog_item import XYCatalogItem
        from common.models.xy_order import XYOrder
        from common.models.xy_account import XYAccount
        from common.services.item_offline_service import batch_offline_items_from_xianyu
        from common.utils.time_utils import get_beijing_now
        from scheduler.app.services.scheduler.scheduled_publish_task import _compute_next_trigger
        from sqlalchemy import select, delete as sa_delete

        logger.debug(f"【{self.task_name}】开始扫描到期规则")

        try:
            async with async_session_maker() as session:
                now = get_beijing_now()
                stmt = select(OfflineSchedule).where(
                    OfflineSchedule.enabled == True,
                    OfflineSchedule.next_trigger_at != None,
                    OfflineSchedule.next_trigger_at <= now,
                )
                due = list((await session.execute(stmt)).scalars().all())

                if not due:
                    logger.debug(f"【{self.task_name}】没有到期规则")
                    return

                logger.info(f"【{self.task_name}】发现 {len(due)} 条到期规则")

                for schedule in due:
                    logger.info(f"【{self.task_name}】执行规则 #{schedule.id}「{schedule.name}」")
                    offlined_titles: list[str] = []
                    try:
                        # 1. 筛选符合条件的商品
                        age_cutoff = now - timedelta(days=schedule.age_days)
                        order_cutoff = now - timedelta(days=schedule.no_order_days)

                        # 最近 Y 天内有订单的 item_id
                        order_stmt = select(XYOrder.item_id).where(
                            XYOrder.item_id.isnot(None),
                            XYOrder.created_at >= order_cutoff,
                        ).distinct()
                        items_with_orders = {row[0] for row in (await session.execute(order_stmt)).all()}

                        # 符合年龄条件的商品，按最早发布排序
                        item_stmt = select(XYCatalogItem.item_id, XYCatalogItem.title).where(
                            XYCatalogItem.owner_id == schedule.user_id,
                            XYCatalogItem.created_at <= age_cutoff,
                        ).order_by(XYCatalogItem.created_at.asc())
                        rows = (await session.execute(item_stmt)).all()

                        candidates = [
                            {"item_id": row.item_id, "title": row.title}
                            for row in rows
                            if row.item_id not in items_with_orders
                        ][:schedule.offline_count]

                        # 标题→item_id 映射，用于下架后记录编号
                        title_map = {c["item_id"]: c["title"] for c in candidates}

                        if not candidates:
                            logger.info(f"【{self.task_name}】规则 #{schedule.id} 无符合条件商品")
                        else:
                            item_ids = [c["item_id"] for c in candidates]
                            logger.info(f"【{self.task_name}】规则 #{schedule.id} 筛选出 {len(item_ids)} 件: {item_ids}")

                            # 2. 对每个账号执行下架
                            for aid in schedule.account_ids:
                                acct_stmt = select(XYAccount).where(
                                    XYAccount.account_id == aid, XYAccount.owner_id == schedule.user_id
                                )
                                account = (await session.execute(acct_stmt)).scalar_one_or_none()
                                if not account or not account.cookie:
                                    logger.warning(f"【{self.task_name}】账号 {aid} 不存在或无 Cookie，跳过")
                                    continue

                                result = await batch_offline_items_from_xianyu(
                                    account_id=aid,
                                    cookies_str=account.cookie,
                                    item_ids=item_ids,
                                )
                                if result.get("success"):
                                    suc = result.get("suc_count", 0)
                                    logger.info(f"【{self.task_name}】账号 {aid} 下架成功 {suc} 件")
                                    for r in result.get("results", []):
                                        if r.get("success"):
                                            title = title_map.get(r["item_id"], r["item_id"])
                                            offlined_titles.append(title)
                                            await session.execute(
                                                sa_delete(XYCatalogItem).where(XYCatalogItem.item_id == r["item_id"])
                                            )
                                else:
                                    logger.warning(f"【{self.task_name}】账号 {aid} 下架失败: {result.get('message')}")

                            await session.commit()

                        # 写执行记录
                        from common.models.offline_schedule_log import OfflineScheduleLog
                        import re as _re
                        codes = [_re.search(r'(A\d+)', t or "").group(1) if _re.search(r'(A\d+)', t or "") else (t or "")[:30] for t in offlined_titles]
                        log_entry = OfflineScheduleLog(
                            schedule_id=schedule.id,
                            executed_at=get_beijing_now(),
                            status="completed",
                            total_count=len(candidates),
                            offlined_count=len(offlined_titles),
                            offlined_items=codes,
                        )
                        session.add(log_entry)
                        await session.commit()
                        logger.info(f"【{self.task_name}】规则 #{schedule.id} 执行记录已写入，下架 {len(codes)} 件: {codes}")

                    except Exception as e:
                        logger.error(f"【{self.task_name}】规则 #{schedule.id} 执行失败: {e}")
                        await session.rollback()

                    # 3. 推进 next_trigger_at
                    try:
                        schedule.last_triggered_at = get_beijing_now()
                        schedule.next_trigger_at = _compute_next_trigger(
                            schedule.schedule_mode, schedule.schedule_config,
                            after=get_beijing_now(),
                        )
                        await session.commit()
                    except Exception as e2:
                        logger.error(f"【{self.task_name}】推进规则 #{schedule.id} 失败: {e2}")
                        await session.rollback()

                    await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"【{self.task_name}】扫描异常: {e}")


offline_schedule_task_service = OfflineScheduleTaskService(task_name="自动下架")
