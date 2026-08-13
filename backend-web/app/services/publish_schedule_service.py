"""
定时发布规则服务

功能：
1. 定时规则 CRUD（创建/查询/更新/删除/开关）
2. next_trigger_at 计算（支持单次/每天/每周 + 指定时间点/时间段随机）
3. 执行记录管理
"""
from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from common.models.publish_schedule import PublishSchedule
from common.models.publish_schedule_log import PublishScheduleLog
from common.utils.time_utils import BEIJING_TZ, get_beijing_now, safe_isoformat


def _compute_next_trigger(schedule_mode: str, schedule_config: dict, after: datetime = None) -> Optional[datetime]:
    """
    根据调度配置计算下一次触发时间。

    schedule_config 结构：
      - once:     {"datetime": "2026-08-01T20:00:00"}
      - daily:    {"times": ["08:00", "20:00"]}  或  {"time_range": {"start":"18:00","end":"22:00"}, "random": true}
      - weekly:   {"days": [1,3,5], "times": ["20:00"]}  或  {"days":[1,3,5], "time_range": {...}, "random": true}

    Args:
        schedule_mode: once / daily / weekly
        schedule_config: 时间配置 JSON
        after: 计算此时间之后的下一次触发，默认当前时间

    Returns:
        下次触发的 datetime，如无法计算（如 once 已过期）返回 None
    """
    now = after if after else get_beijing_now()
    today = now.date()

    if schedule_mode == "once":
        dt_str = schedule_config.get("datetime")
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None
        # 确保时区一致：如果解析的是 naive datetime，补上北京时间
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TZ)
        # 如果时间已过，返回 None（调用方应禁用规则）
        if dt <= now:
            return None
        return dt

    if schedule_mode == "daily":
        return _next_daily(schedule_config, now, today)

    if schedule_mode == "weekly":
        return _next_weekly(schedule_config, now, today)

    return None


def _next_daily(config: dict, now: datetime, today) -> Optional[datetime]:
    """计算每天的 next_trigger_at"""
    use_random = config.get("random", False)
    time_range = config.get("time_range")
    times = config.get("times", [])

    if use_random and time_range:
        # 时间段随机：每次计算都随机一个时间点
        start_str = time_range.get("start", "00:00")
        end_str = time_range.get("end", "23:59")
        trigger_time = _random_time_between(start_str, end_str)
        candidate = datetime.combine(today, trigger_time).replace(tzinfo=BEIJING_TZ)
        if candidate <= now:
            # 今天已过，算明天的
            candidate = datetime.combine(today + timedelta(days=1), _random_time_between(start_str, end_str)).replace(tzinfo=BEIJING_TZ)
        return candidate

    if times:
        # 指定时间点列表：找到今天第一个未过的时间
        time_points = [_parse_time(t) for t in times if _parse_time(t) is not None]
        time_points.sort()
        for tp in time_points:
            candidate = datetime.combine(today, tp).replace(tzinfo=BEIJING_TZ)
            if candidate > now:
                return candidate
        # 今天都过了，取明天第一个时间点
        if time_points:
            return datetime.combine(today + timedelta(days=1), time_points[0]).replace(tzinfo=BEIJING_TZ)

    # 默认 00:00
    candidate = datetime.combine(today, time(0, 0)).replace(tzinfo=BEIJING_TZ)
    if candidate <= now:
        candidate = datetime.combine(today + timedelta(days=1), time(0, 0)).replace(tzinfo=BEIJING_TZ)
    return candidate


def _next_weekly(config: dict, now: datetime, today) -> Optional[datetime]:
    """计算每周的 next_trigger_at"""
    days = config.get("days", [])
    if not days:
        return None

    use_random = config.get("random", False)
    time_range = config.get("time_range")
    times = config.get("times", [])

    for offset in range(8):  # 最多查一周
        candidate_date = today + timedelta(days=offset)
        candidate_weekday = candidate_date.isoweekday()

        if candidate_weekday in days:
            # 如果就是今天，检查时间是否已过
            if offset == 0:
                if use_random and time_range:
                    trigger_time = _random_time_between(time_range.get("start", "00:00"), time_range.get("end", "23:59"))
                    candidate = datetime.combine(candidate_date, trigger_time).replace(tzinfo=BEIJING_TZ)
                    if candidate > now:
                        return candidate
                    continue  # 今天已过，找下一个
                if times:
                    time_points = [_parse_time(t) for t in times if _parse_time(t) is not None]
                    time_points.sort()
                    for tp in time_points:
                        candidate = datetime.combine(candidate_date, tp).replace(tzinfo=BEIJING_TZ)
                        if candidate > now:
                            return candidate
                    continue  # 今天已过，找下一个
                # 默认用 00:00
                candidate = datetime.combine(candidate_date, time(0, 0)).replace(tzinfo=BEIJING_TZ)
                if candidate > now:
                    return candidate
                continue

            # 不是今天，直接用第一个时间点
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


def _parse_time(t_str: str) -> Optional[time]:
    """解析 HH:MM 格式的时间字符串"""
    try:
        h, m = t_str.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _random_time_between(start_str: str, end_str: str) -> time:
    """在时间段内随机生成一个时间点（精确到分钟）"""
    start = _parse_time(start_str) or time(0, 0)
    end = _parse_time(end_str) or time(23, 59)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        end_minutes = start_minutes + 1  # 至少间隔1分钟
    random_minutes = random.randint(start_minutes, end_minutes)
    return time(random_minutes // 60, random_minutes % 60)


DEFAULT_PUBLISH_CONFIG = {"publish_mode": "specified", "random_count": 1, "deduplicate": True}


def _normalize_publish_config(raw: dict | None) -> dict:
    """规范化发布配置，填充默认值"""
    cfg = dict(raw) if (raw is not None and isinstance(raw, dict)) else {}
    if "publish_mode" not in cfg or cfg["publish_mode"] not in ("specified", "random"):
        cfg["publish_mode"] = "specified"
    cfg.setdefault("random_count", 1)
    cfg.setdefault("deduplicate", True)
    return cfg


def _schedule_to_dict(s: PublishSchedule) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "name": s.name,
        "schedule_mode": s.schedule_mode,
        "schedule_config": s.schedule_config or {},
        "account_ids": s.account_ids or [],
        "material_ids": s.material_ids or [],
        "publish_config": _normalize_publish_config(s.publish_config),
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
        "detail_json": l.detail_json,
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
            publish_config=_normalize_publish_config(data.get("publish_config")),
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
            "account_ids", "material_ids", "enabled", "publish_config",
        ]
        for field in updatable:
            if field in data and data[field] is not None:
                if field == "publish_config":
                    setattr(schedule, field, _normalize_publish_config(data[field]))
                else:
                    setattr(schedule, field, data[field])

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
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    # ========== 执行日志 ==========

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

    async def resolve_publish_material_ids(self, schedule: PublishSchedule, account_id: str | None = None) -> list[int]:
        """先去重后随机选取素材（发布前刷新账号商品列表确保去重准确）

        若 publish_mode == "specified"：直接返回全部 material_ids
        若 publish_mode == "random"：随机选取 random_count 条；若 deduplicate == True，
        先刷新账号商品列表再过滤已存在编号。
        """
        import re as _re
        from common.models.product_material import ProductMaterial
        from common.models.xy_catalog_item import XYCatalogItem
        from common.models.xy_account import XYAccount
        from common.services.item_service import ItemService
        from sqlalchemy import select as sa_select

        cfg = _normalize_publish_config(schedule.publish_config)
        material_ids = list(schedule.material_ids)

        if cfg["publish_mode"] != "random":
            return material_ids

        random_count = max(1, int(cfg.get("random_count", 1)))
        deduplicate = bool(cfg.get("deduplicate", True))
        available = list(material_ids)

        if not deduplicate:
            return random.sample(available, min(random_count, len(available)))

        # ====== 去重逻辑 ======

        # 1. 刷新每个账号的商品列表
        for aid in schedule.account_ids:
            try:
                stmt = sa_select(XYAccount).where(
                    XYAccount.account_id == aid, XYAccount.owner_id == schedule.user_id
                )
                account = (await self.session.execute(stmt)).scalar_one_or_none()
                if account:
                    item_svc = ItemService(self.session)
                    await item_svc.fetch_all_items_from_account(account=account)
            except Exception:
                pass

        # 2. 查询账号现有商品编号（先做 account_id 字符串 → 主键映射）
        code_pattern = _re.compile(r'^(A\d+)')
        # xy_catalog_items.account_id 存的是 xy_accounts 的主键，不是 account_id 字符串
        acct_stmt = sa_select(XYAccount.id).where(
            XYAccount.account_id.in_(schedule.account_ids)
        )
        acct_rows = (await self.session.execute(acct_stmt)).all()
        acct_pks = [row[0] for row in acct_rows]
        logger.info(
            f"[定时发布去重] 🔍 account_id 字符串→主键: "
            f"{dict(zip(schedule.account_ids, acct_pks))}"
        )
        stmt = sa_select(XYCatalogItem.title).where(
            XYCatalogItem.account_pk.in_(acct_pks),
        )
        item_rows = (await self.session.execute(stmt)).all()
        logger.info(f"[定时发布去重] 🔍 查询到 {len(item_rows)} 条商品记录")
        existing_codes = set()
        for row in item_rows:
            m = code_pattern.search(row.title or "")
            if m:
                existing_codes.add(m.group(1))
        logger.info(
            f"[定时发布去重] 🔍 账号现有商品编号: "
            f"{sorted(existing_codes)}" if existing_codes else "（无）"
        )

        # 3. 素材标题 → 编号
        stmt = sa_select(ProductMaterial.id, ProductMaterial.title).where(
            ProductMaterial.id.in_(available)
        )
        mat_rows = (await self.session.execute(stmt)).all()
        mat_code_map = {}
        for row in mat_rows:
            m = code_pattern.search(row.title or "")
            if m:
                mat_code_map[row.id] = m.group(1)
        logger.info(
            f"[定时发布去重] 🔍 素材编号映射: "
            f"{ {mid: code for mid, code in mat_code_map.items()} }" if mat_code_map else "（无素材有编号）"
        )

        # 4. 过滤
        kept = []
        removed = []
        for mid in available:
            mat_code = mat_code_map.get(mid)
            if mat_code in existing_codes:
                removed.append(f"{mid}({mat_code})")
            else:
                kept.append(mid)
        if removed:
            logger.info(f"[定时发布去重] 🔍 过滤掉的素材: {removed}")
        available = kept

        if not available:
            return []

        sel = random.sample(available, min(random_count, len(available)))
        sel_codes = [(mid, mat_code_map.get(mid, "无编号")) for mid in sel]
        logger.info(f"[定时发布去重] 🔍 最终选中: {sel_codes}")
        return sel


# 计算函数导出供外部使用
__all__ = [
    "PublishScheduleService",
    "_compute_next_trigger",
]
