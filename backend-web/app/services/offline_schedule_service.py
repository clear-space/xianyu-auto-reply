"""
自动下架规则服务

功能：
1. 下架规则 CRUD（创建/查询/更新/删除/开关）
2. next_trigger_at 计算（复用 publish_schedule_service 的时间函数）
3. 按「上架天数 + 无订单天数」筛选商品
4. 批量下架 + 删除数据库记录
"""
from __future__ import annotations

import random as _random_module
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from common.models.offline_schedule import OfflineSchedule
from common.utils.time_utils import get_beijing_now, safe_isoformat
from app.services.publish_schedule_service import _compute_next_trigger


def _offline_to_dict(s: OfflineSchedule) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "name": s.name,
        "age_days": s.age_days,
        "no_order_days": s.no_order_days,
        "offline_count": s.offline_count,
        "schedule_mode": s.schedule_mode,
        "schedule_config": s.schedule_config or {},
        "account_ids": s.account_ids or [],
        "enabled": s.enabled,
        "last_triggered_at": safe_isoformat(s.last_triggered_at),
        "next_trigger_at": safe_isoformat(s.next_trigger_at),
        "created_at": safe_isoformat(s.created_at),
        "updated_at": safe_isoformat(s.updated_at),
    }


class OfflineScheduleService:
    """自动下架规则 CRUD + 执行"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ========== CRUD ==========

    async def create(self, user_id: int, data: dict) -> OfflineSchedule:
        schedule = OfflineSchedule(
            user_id=user_id,
            name=data["name"],
            age_days=data.get("age_days", 7),
            no_order_days=data.get("no_order_days", 7),
            offline_count=data.get("offline_count", 5),
            schedule_mode=data.get("schedule_mode", "daily"),
            schedule_config=data.get("schedule_config", {}),
            account_ids=data.get("account_ids", []),
            enabled=data.get("enabled", True),
        )
        schedule.next_trigger_at = _compute_next_trigger(schedule.schedule_mode, schedule.schedule_config)
        self.session.add(schedule)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def list_schedules(self, user_id: int = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        conds = []
        if user_id is not None:
            conds.append(OfflineSchedule.user_id == user_id)
        count_stmt = select(func.count()).select_from(OfflineSchedule).where(*conds)
        total = (await self.session.execute(count_stmt)).scalar() or 0
        stmt = (
            select(OfflineSchedule).where(*conds)
            .order_by(desc(OfflineSchedule.created_at))
            .offset((page - 1) * page_size).limit(page_size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {
            "list": [_offline_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    async def get(self, schedule_id: int, user_id: int = None) -> Optional[OfflineSchedule]:
        conds = [OfflineSchedule.id == schedule_id]
        if user_id is not None:
            conds.append(OfflineSchedule.user_id == user_id)
        return (await self.session.execute(select(OfflineSchedule).where(*conds))).scalar_one_or_none()

    async def update(self, schedule_id: int, user_id: int = None, data: dict = None) -> Optional[OfflineSchedule]:
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return None
        for field in ["name", "age_days", "no_order_days", "offline_count", "schedule_mode", "schedule_config", "account_ids", "enabled"]:
            if field in data and data[field] is not None:
                setattr(schedule, field, data[field])
        schedule.next_trigger_at = _compute_next_trigger(schedule.schedule_mode, schedule.schedule_config)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def delete(self, schedule_id: int, user_id: int = None) -> bool:
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return False
        await self.session.delete(schedule)
        await self.session.commit()
        return True

    async def toggle(self, schedule_id: int, user_id: int = None) -> Optional[OfflineSchedule]:
        schedule = await self.get(schedule_id, user_id)
        if not schedule:
            return None
        schedule.enabled = not schedule.enabled
        if schedule.enabled:
            schedule.next_trigger_at = _compute_next_trigger(schedule.schedule_mode, schedule.schedule_config)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    # ========== 核心：筛选 + 下架 ==========

    async def find_items_to_offline(self, schedule: OfflineSchedule) -> list[dict]:
        """按年龄+订单筛选条件找出需要下架的商品，返回 [{item_id, title}, ...]"""
        from common.models.xy_catalog_item import XYCatalogItem
        from common.models.xy_order import XYOrder

        cutoff_date = get_beijing_now() - timedelta(days=schedule.age_days)
        order_cutoff = get_beijing_now() - timedelta(days=schedule.no_order_days)

        # 最近 Y 天内有订单的 item_id
        order_stmt = select(XYOrder.item_id).where(
            XYOrder.item_id.isnot(None),
            XYOrder.created_at >= order_cutoff,
        ).distinct()
        items_with_orders = {row[0] for row in (await self.session.execute(order_stmt)).all()}

        # 符合年龄条件的商品（按 owner_id 过滤，按 created_at 升序 = 最早发布排前面）
        stmt = select(XYCatalogItem.item_id, XYCatalogItem.title).where(
            XYCatalogItem.owner_id == schedule.user_id,
            XYCatalogItem.created_at <= cutoff_date,
        ).order_by(XYCatalogItem.created_at.asc())

        rows = (await self.session.execute(stmt)).all()

        candidates = [
            {"item_id": row.item_id, "title": row.title}
            for row in rows
            if row.item_id not in items_with_orders
        ]

        # 取前 Z 个
        selected = candidates[:schedule.offline_count]
        return selected

    async def execute_offline(self, schedule: OfflineSchedule) -> dict:
        """对筛选出的商品执行下架 + 数据库删除"""
        import re as _re
        from common.models.xy_account import XYAccount
        from common.models.xy_catalog_item import XYCatalogItem
        from common.models.offline_schedule_log import OfflineScheduleLog
        from sqlalchemy import delete as sa_delete
        from common.services.item_offline_service import batch_offline_items_from_xianyu

        items = await self.find_items_to_offline(schedule)
        if not items:
            logger.info(f"[自动下架] 规则 #{schedule.id}「{schedule.name}」无符合条件的商品")
            log_entry = OfflineScheduleLog(
                schedule_id=schedule.id,
                executed_at=get_beijing_now(),
                status="completed",
                total_count=0,
                offlined_count=0,
                offlined_items=[],
            )
            self.session.add(log_entry)
            await self.session.commit()
            return {"success": True, "offlined": 0, "message": "无符合条件商品"}

        item_ids = [it["item_id"] for it in items]
        title_map = {it["item_id"]: it["title"] for it in items}
        logger.info(f"[自动下架] 规则 #{schedule.id}「{schedule.name}」筛选出 {len(items)} 件: {item_ids}")

        offlined_titles: list[str] = []
        total_offlined = 0
        for aid in schedule.account_ids:
            stmt = select(XYAccount).where(XYAccount.account_id == aid, XYAccount.owner_id == schedule.user_id)
            account = (await self.session.execute(stmt)).scalar_one_or_none()
            if not account or not account.cookie:
                logger.warning(f"[自动下架] 账号 {aid} 不存在或无 Cookie，跳过")
                continue

            result = await batch_offline_items_from_xianyu(
                account_id=aid,
                cookies_str=account.cookie,
                item_ids=item_ids,
            )
            if result.get("success"):
                total_offlined += result.get("suc_count", 0)
                for r in result.get("results", []):
                    if r.get("success"):
                        offlined_titles.append(title_map.get(r["item_id"], r["item_id"]))
                        await self.session.execute(
                            sa_delete(XYCatalogItem).where(XYCatalogItem.item_id == r["item_id"])
                        )
            else:
                logger.warning(f"[自动下架] 账号 {aid} 下架失败: {result.get('message')}")

        codes = [_re.search(r'(A\d+)', t or "").group(1) if _re.search(r'(A\d+)', t or "") else (t or "")[:30] for t in offlined_titles]
        log_entry = OfflineScheduleLog(
            schedule_id=schedule.id,
            executed_at=get_beijing_now(),
            status="completed",
            total_count=len(items),
            offlined_count=total_offlined,
            offlined_items=codes,
        )
        self.session.add(log_entry)
        await self.session.commit()
        logger.info(f"[自动下架] 规则 #{schedule.id} 完成: 下架 {total_offlined}/{len(items)} 件, 编号 {codes}")
        return {"success": True, "offlined": total_offlined, "total": len(items), "codes": codes, "message": f"下架 {total_offlined}/{len(items)} 件"}

    # ========== Scheduler 专用 ==========

    async def get_due_schedules(self) -> List[OfflineSchedule]:
        now = get_beijing_now()
        stmt = select(OfflineSchedule).where(
            OfflineSchedule.enabled == True,
            OfflineSchedule.next_trigger_at != None,
            OfflineSchedule.next_trigger_at <= now,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def advance_schedule(self, schedule: OfflineSchedule) -> None:
        schedule.last_triggered_at = get_beijing_now()
        schedule.next_trigger_at = _compute_next_trigger(schedule.schedule_mode, schedule.schedule_config, after=get_beijing_now())
        await self.session.commit()
        await self.session.refresh(schedule)
