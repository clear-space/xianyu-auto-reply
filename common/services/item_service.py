"""
商品服务

功能：
1. 商品目录CRUD操作
2. 商品信息更新（标题、价格、描述等）
3. 商品列表查询
4. 批量删除商品
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Set

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from common.db.redis_client import distributed_lock
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.default_reply import DefaultReply
from common.models.card import Card


def normalize_publish_time(value) -> str | None:
    """把闲鱼返回的上架时间归一化为带时区的 ISO 字符串（支持毫秒/秒时间戳与常见字符串格式）"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:
                ts /= 1000  # 毫秒时间戳
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if isinstance(value, str):
            digits = value.strip()
            if digits.isdigit():
                ts = float(digits)
                if ts > 1e12:
                    ts /= 1000
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            for fmt in (
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
            ):
                try:
                    parsed = datetime.strptime(digits, fmt)
                except ValueError:
                    continue
                return parsed.replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


# 商品状态语义值：列表分组写入 int（0=在售, 1=已售出），对账写入字符串（offline/deleted/inactive/unknown）
_ITEM_STATUS_INT_MAP = {0: "on_sale", 1: "sold"}
_VALID_STATUS_STRINGS = ("on_sale", "sold", "offline", "deleted", "inactive", "unknown")


def _normalize_item_status(raw) -> str:
    """把本地存储的商品状态归一化为语义枚举：on_sale/sold/offline/deleted/inactive/unknown"""
    if isinstance(raw, bool):
        return "unknown"
    if isinstance(raw, int):
        return _ITEM_STATUS_INT_MAP.get(raw, "unknown")
    if isinstance(raw, str):
        s = raw.strip()
        if s in _VALID_STATUS_STRINGS:
            return s
        try:
            return _ITEM_STATUS_INT_MAP.get(int(s), "unknown")
        except ValueError:
            return "unknown"
    return "unknown"


def get_item_publish_time(metadata_json: dict | None, created_at) -> datetime | None:
    """取商品上架时间（aware datetime）；metadata 缺失时兜底用本地首次入库时间。

    供自动下架规则筛选使用（上架时间早于 X 天前）。
    注意：xy_catalog_items.created_at 入库时为 UTC 墙钟时间，MySQL DATETIME 读回是
    naive 的，统一按 UTC 归一化为 aware，避免与北京时区截止时间比较时报错。
    """
    if metadata_json:
        s = metadata_json.get("publish_time")
        if s:
            try:
                dt = datetime.fromisoformat(str(s))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass
    if created_at is None:
        return None
    if getattr(created_at, "tzinfo", None) is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at


class ItemService:
    """Read/write operations for catalog items."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _resolve_account_fetch_user_id(self, account: XYAccount) -> str:
        from common.utils.xianyu_utils import extract_account_user_id_from_cookie

        cookie_user_id = extract_account_user_id_from_cookie(account.cookie)
        stored_user_id = str(account.unb or "").strip()
        fallback_user_id = str(account.account_id or "").strip()
        resolved_user_id = cookie_user_id or stored_user_id or fallback_user_id

        if cookie_user_id and cookie_user_id != stored_user_id:
            logger.warning(
                f"账号[{account.account_id}]库内unb[{stored_user_id or '-'}]与当前Cookie账号[{cookie_user_id}]不一致，本次同步将按Cookie账号抓取商品"
            )

        return resolved_user_id

    def _collect_valid_item_entries(self, items: list[dict]) -> tuple[list[tuple[str, dict]], int]:
        valid_items = []
        skipped_count = 0
        for item in items:
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id.startswith("auto_"):
                skipped_count += 1
                continue
            valid_items.append((item_id, item))
        return valid_items, skipped_count

    async def _get_existing_item_map(
        self,
        account: XYAccount,
        item_ids: list[str],
    ) -> dict[str, XYCatalogItem]:
        if not item_ids:
            return {}

        stmt = select(XYCatalogItem).where(
            XYCatalogItem.owner_id == account.owner_id,
            XYCatalogItem.account_pk == account.id,
            XYCatalogItem.item_id.in_(item_ids),
        )
        existing_rows = (await self.session.execute(stmt)).scalars().all()
        return {row.item_id: row for row in existing_rows}

    async def get_existing_item_ids_for_account(
        self,
        account: XYAccount,
        item_ids: list[str],
    ) -> set[str]:
        """返回指定账号本地商品库中实际存在的商品 ID 集合。

        Args:
            account: 已完成用户权限校验的账号对象。
            item_ids: 待校验的商品 ID 列表。
        Returns:
            同时属于该账号且存在于本地商品库的商品 ID 集合。
        """
        return set((await self._get_existing_item_map(account, item_ids)).keys())

    async def list_items(self, owner_id: int | None, account_id: str | None = None) -> list[dict]:
        """获取商品列表
        
        Args:
            owner_id: 用户ID，None表示查询所有用户（管理员）
            account_id: 账号ID（可选）
        """
        stmt = (
            select(XYCatalogItem, XYAccount.account_id)
            .outerjoin(XYAccount, XYCatalogItem.account_pk == XYAccount.id)
            .order_by(XYCatalogItem.created_at.desc())
        )
        if owner_id is not None:
            stmt = stmt.where(XYCatalogItem.owner_id == owner_id)
        if account_id:
            stmt = stmt.where(XYAccount.account_id == account_id)
        rows = await self.session.execute(stmt)
        items_data = rows.all()
        
        # 批量查询所有商品的默认回复状态和卡券状态
        default_reply_map = await self._get_default_reply_status_batch(items_data)
        card_set = await self._get_card_status_batch(items_data)
        
        return [self._serialize_item(item, acct_id, default_reply_map.get((acct_id, item.item_id)), item.item_id in card_set) for item, acct_id in items_data]

    async def list_items_paginated(
        self,
        owner_id: int | None,
        account_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        is_polished: bool | None = None,
        is_multi_spec: bool | None = None,
        multi_quantity_delivery: bool | None = None,
        item_status: str | None = None,
        sort_by: str | None = None,
        sort_order: str = "desc",
    ) -> tuple[list[dict], int]:
        """获取商品列表（分页），支持多条件筛选与排序

        Args:
            owner_id: 用户ID，None表示查询所有用户（管理员）
            account_id: 账号ID（可选）
            page: 页码
            page_size: 每页数量
            keyword: 关键字（支持商品ID、标题、详情）
            is_polished: 是否擦亮筛选
            sort_by: 排序字段（created_at/updated_at/price 基础字段；
                     days_on_shelf/show_pv/ipv/want_count/post_dt 快照字段）
            sort_order: asc/desc（默认 desc）
            is_multi_spec: 多规格筛选
            multi_quantity_delivery: 多数量发货筛选
            
        Returns:
            (商品列表, 总数)
        """
        from sqlalchemy import String, and_, cast, func, or_
        
        base_stmt = (
            select(XYCatalogItem, XYAccount.account_id)
            .outerjoin(XYAccount, XYCatalogItem.account_pk == XYAccount.id)
        )
        
        conditions = []
        if owner_id is not None:
            conditions.append(XYCatalogItem.owner_id == owner_id)
        if account_id:
            conditions.append(XYAccount.account_id == account_id)
        if keyword and keyword.strip():
            keyword_like = f"%{keyword.strip()}%"
            conditions.append(
                or_(
                    XYCatalogItem.item_id.like(keyword_like),
                    XYCatalogItem.title.like(keyword_like),
                    cast(XYCatalogItem.metadata_json, String).like(keyword_like),
                )
            )
        
        # 是否擦亮筛选（直接字段）
        if is_polished is not None:
            conditions.append(XYCatalogItem.is_polished == is_polished)
        
        # 多规格筛选（metadata_json字段）
        if is_multi_spec is not None:
            if is_multi_spec:
                conditions.append(
                    XYCatalogItem.metadata_json["is_multi_spec"].as_boolean() == True
                )
            else:
                conditions.append(
                    or_(
                        XYCatalogItem.metadata_json.is_(None),
                        XYCatalogItem.metadata_json["is_multi_spec"].as_boolean() == False,
                        XYCatalogItem.metadata_json["is_multi_spec"].is_(None)
                    )
                )
        
        # 多数量发货筛选（metadata_json字段）
        if multi_quantity_delivery is not None:
            if multi_quantity_delivery:
                conditions.append(
                    XYCatalogItem.metadata_json["multi_quantity_delivery"].as_boolean() == True
                )
            else:
                conditions.append(
                    or_(
                        XYCatalogItem.metadata_json.is_(None),
                        XYCatalogItem.metadata_json["multi_quantity_delivery"].as_boolean() == False,
                        XYCatalogItem.metadata_json["multi_quantity_delivery"].is_(None)
                    )
                )

        # 商品状态筛选（metadata_json.item_status：int 0/1 或字符串 offline/deleted/unknown）
        if item_status:
            status_expr = func.json_unquote(
                func.json_extract(XYCatalogItem.metadata_json, "$.item_status")
            )
            if item_status == "unknown":
                conditions.append(
                    or_(
                        XYCatalogItem.metadata_json.is_(None),
                        status_expr.is_(None),
                        status_expr == "unknown",
                    )
                )
            elif item_status == "on_sale":
                conditions.append(status_expr == "0")
            elif item_status == "sold":
                conditions.append(status_expr == "1")
            else:
                conditions.append(status_expr == item_status)

        if conditions:
            base_stmt = base_stmt.where(and_(*conditions))
        
        # 查询总数：仅在按账号筛选时才需要 JOIN 账号表，否则直接基于商品表统计，避免无谓 JOIN
        count_stmt = select(func.count(XYCatalogItem.id)).select_from(XYCatalogItem)
        if account_id:
            count_stmt = count_stmt.outerjoin(XYAccount, XYCatalogItem.account_pk == XYAccount.id)
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total_result = await self.session.execute(count_stmt)
        total = total_result.scalar() or 0
        
        # 分页查询
        offset = (page - 1) * page_size
        # 排序：快照字段需 LEFT JOIN 最新快照行（每商品 stat_date 最大的一行），NULL 排最后
        from sqlalchemy import Numeric
        from sqlalchemy.orm import aliased

        from common.models.item_stats_daily import ItemStatsDaily

        sort_desc = (sort_order or "desc").lower() != "asc"
        order_col = None
        stats_alias = None
        if sort_by in ("days_on_shelf", "show_pv", "ipv", "want_count", "post_dt"):
            stats_col_map = {
                "days_on_shelf": "days_on_shelf",
                "show_pv": "show_pv_7d",
                "ipv": "ipv_7d",
                "want_count": "want_count",
                "post_dt": "post_dt",
            }
            max_date_subq = (
                select(ItemStatsDaily.item_id, func.max(ItemStatsDaily.stat_date).label("max_date"))
                .group_by(ItemStatsDaily.item_id)
                .subquery()
            )
            stats_alias = aliased(ItemStatsDaily)
            base_stmt = (
                base_stmt.outerjoin(
                    max_date_subq, XYCatalogItem.item_id == max_date_subq.c.item_id
                ).outerjoin(
                    stats_alias,
                    and_(
                        stats_alias.item_id == max_date_subq.c.item_id,
                        stats_alias.stat_date == max_date_subq.c.max_date,
                    ),
                )
            )
            order_col = getattr(stats_alias, stats_col_map[sort_by])
        elif sort_by == "price":
            # 价格列为字符串且部分数据带货币符号（如 "¥1"），剥离符号后再转数值排序
            order_col = cast(
                func.replace(func.replace(XYCatalogItem.price, "¥", ""), "￥", ""),
                Numeric(12, 2),
            )
        elif sort_by in ("created_at", "updated_at"):
            order_col = getattr(XYCatalogItem, sort_by)

        if order_col is None:
            order_col = XYCatalogItem.created_at

        order_exprs = []
        if stats_alias is not None:
            # MySQL 中 NULL 默认排最前，用 is_(None) 显式把无快照商品排到最后
            order_exprs.append(order_col.is_(None))
        order_exprs.append(order_col.desc() if sort_desc else order_col.asc())
        stmt = base_stmt.order_by(*order_exprs).offset(offset).limit(page_size)
        rows = await self.session.execute(stmt)
        items_data = rows.all()
        
        # 批量查询所有商品的默认回复状态和卡券状态
        default_reply_map = await self._get_default_reply_status_batch(items_data)
        card_set = await self._get_card_status_batch(items_data)
        
        items = [self._serialize_item(item, acct_id, default_reply_map.get((acct_id, item.item_id)), item.item_id in card_set) for item, acct_id in items_data]
        return items, total

    async def fetch_items_page_from_account(
        self,
        account: XYAccount,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """从指定账号抓取单页商品并入库"""
        from common.utils.item_info_manager import ItemInfoManager

        myid = self._resolve_account_fetch_user_id(account)

        manager = ItemInfoManager(account.account_id, account.cookie)
        try:
            result = await manager.get_item_list_info(page, page_size, myid=myid)
        except Exception as exc:
            return {"success": False, "message": f"获取商品失败: {exc}"}
        finally:
            await manager.close()

        if not result or not result.get("success"):
            message = ""
            if isinstance(result, dict):
                message = result.get("message") or result.get("error") or ""
            return {"success": False, "message": message or "获取商品失败"}

        items = result.get("items") or []
        count = result.get("current_count") or len(items)

        try:
            saved_count, _ = await self.save_fetched_items(account, items)
        except Exception as exc:
            await self.session.rollback()
            return {"success": False, "message": f"保存商品失败: {exc}"}

        return {
            "success": True,
            "message": f"获取到第 {page} 页商品，共 {count} 件",
            "items": items,
            "page": page,
            "page_number": page,
            "page_size": page_size,
            "count": count,
            "current_count": count,
            "has_more": len(items) >= page_size,
            "saved_count": saved_count,
        }

    async def fetch_all_items_from_account(
        self,
        account: XYAccount,
        page_size: int = 20,
        max_pages: int | None = None,
        stop_when_page_all_existing: bool = False,
        required_title_keyword: str | None = None,
    ) -> dict[str, Any]:
        """抓取指定账号全部商品并入库（账号级加锁入口）

        通过 Redis 账号级互斥锁，保证同一账号同一时刻只有一个商品同步流程在
        拉取 + 落库，避免「定时获取闲鱼商品任务」与「商品管理页手动触发同步」
        并发 upsert 同一商品。Redis 不可用时降级为无锁执行，由 xy_catalog_items
        的 (account_id, item_id) 唯一约束 + 保存时的冲突重试做最终兜底。
        """
        lock_name = f"item_sync:{account.account_id}"
        try:
            async with distributed_lock(
                lock_name, expire=300, blocking=True, timeout=8
            ) as lock:
                if not lock.is_locked:
                    logger.info(
                        f"账号[{account.account_id}]商品同步锁被占用，跳过本次"
                        f"（避免与其他同步任务并发）"
                    )
                    return {
                        "success": True,
                        "skipped": True,
                        "message": "账号商品同步锁被占用，已跳过",
                        "items": [],
                        "total_count": 0,
                        "total_pages": 0,
                        "page_size": page_size,
                        "saved_count": 0,
                    }
                return await self._fetch_all_items_from_account_impl(
                    account=account,
                    page_size=page_size,
                    max_pages=max_pages,
                    stop_when_page_all_existing=stop_when_page_all_existing,
                    required_title_keyword=required_title_keyword,
                )
        except Exception as exc:
            # Redis 不可用等异常时降级为无锁执行，靠唯一约束兜底防止重复入库
            logger.warning(
                f"账号[{account.account_id}]商品同步获取锁异常，降级无锁执行"
                f"（依赖唯一约束兜底）: {exc}"
            )
            return await self._fetch_all_items_from_account_impl(
                account=account,
                page_size=page_size,
                max_pages=max_pages,
                stop_when_page_all_existing=stop_when_page_all_existing,
                required_title_keyword=required_title_keyword,
            )

    async def _fetch_all_items_from_account_impl(
        self,
        account: XYAccount,
        page_size: int = 20,
        max_pages: int | None = None,
        stop_when_page_all_existing: bool = False,
        required_title_keyword: str | None = None,
    ) -> dict[str, Any]:
        """抓取指定账号全部商品并入库（实际实现，调用方需已持有账号锁）"""
        from common.utils.item_info_manager import ItemInfoManager

        myid = self._resolve_account_fetch_user_id(account)
        normalized_required_title_keyword = str(required_title_keyword or "").strip()

        manager = ItemInfoManager(account.account_id, account.cookie)
        fetched_items: list[dict] = []
        total_saved_count = 0
        fetched_pages = 0
        matched_required_title_keyword = False
        groups_complete = False  # 两个分组都成功抓完才允许对账（防误标）
        early_break = False  # 任一分组因「整页已存在」提前停止翻页（增量同步，抓取不完整）
        try:
            # 0. 动态获取商品分组（在售/已售出），分组ID按账号变化，不写死
            target_groups: list[tuple[str, Any]] = [("在售", None)]
            try:
                group_result = await manager.get_item_list_info(
                    1, 5, myid=myid, need_group_info=True
                )
                if group_result and group_result.get("success"):
                    raw = group_result.get("raw_data") or {}
                    group_map: dict[str, Any] = {}
                    for g in (raw.get("itemGroupList") or []):
                        name = g.get("groupName")
                        gid = g.get("groupId")
                        if name in ("在售", "已售出") and gid is not None:
                            group_map[name] = gid
                    if group_map:
                        target_groups = [
                            (name, group_map.get(name)) for name in ("在售", "已售出")
                        ]
                        logger.info(f"账号[{account.account_id}]商品分组: {list(group_map.items())}")
                    else:
                        logger.warning(
                            f"账号[{account.account_id}]未获取到商品分组，仅抓取在售分组（跳过对账）"
                        )
                else:
                    logger.warning(
                        f"账号[{account.account_id}]获取商品分组失败，仅抓取在售分组（跳过对账）"
                    )
            except Exception as exc:
                logger.warning(f"账号[{account.account_id}]获取商品分组异常（回退仅抓在售）: {exc}")

            for group_name, group_id in target_groups:
                group_status = 0 if group_name == "在售" else 1  # 0=在售, 1=已售出
                page_number = 1
                while True:
                    if max_pages and page_number > max_pages:
                        logger.info(
                            f"账号[{account.account_id}]商品同步达到最大页数限制 {max_pages}，停止获取"
                        )
                        break

                    logger.info(
                        f"账号[{account.account_id}]正在获取「{group_name}」分组第 {page_number} 页"
                    )
                    result = await manager.get_item_list_info(
                        page_number, page_size, myid=myid,
                        group_name=group_name, group_id=group_id,
                    )

                    if not result or not result.get("success"):
                        message = ""
                        if isinstance(result, dict):
                            message = result.get("message") or result.get("error") or ""
                        logger.error(
                            f"账号[{account.account_id}]商品同步获取「{group_name}」第 {page_number} 页失败: {result}"
                        )
                        return {
                            "success": False,
                            "message": message or f"获取第 {page_number} 页商品失败",
                        }

                    items = result.get("items") or []
                    if not items:
                        logger.info(
                            f"账号[{account.account_id}]「{group_name}」分组第 {page_number} 页无数据，结束该分组"
                        )
                        break

                    # 标记分组状态（供状态列判定：0=在售, 1=已售出）
                    for it in items:
                        it["item_status"] = group_status

                    valid_items, skipped_count = self._collect_valid_item_entries(items)
                    unique_item_ids = list(dict.fromkeys(item_id for item_id, _ in valid_items))
                    existing_map = await self._get_existing_item_map(account, unique_item_ids)
                    page_matches_required_title = (
                        bool(normalized_required_title_keyword)
                        and any(
                            normalized_required_title_keyword in str(item.get("title") or "")
                            for _, item in valid_items
                        )
                    )
                    if page_matches_required_title:
                        matched_required_title_keyword = True
                    page_all_existing = (
                        skipped_count == 0
                        and bool(unique_item_ids)
                        and len(existing_map) == len(unique_item_ids)
                    )

                    try:
                        saved_count, page_changed_count = await self.save_fetched_items(
                            account,
                            items,
                        )
                    except Exception as exc:
                        await self.session.rollback()
                        return {"success": False, "message": f"保存商品失败: {exc}"}
                    fetched_items.extend(items)
                    total_saved_count += saved_count
                    fetched_pages += 1

                    logger.info(
                        f"账号[{account.account_id}]「{group_name}」第{page_number}页完成，本页{len(items)}件，"
                        f"累计抓取{len(fetched_items)}件，整页已存在={page_all_existing}，"
                        f"命中目标商品={page_matches_required_title}"
                    )

                    # 仅当本页全部商品已存在且无实际字段变更（如价格/标题变化）时才停止翻页；
                    # 若有商品被更新（如卖家在闲鱼改价），需继续翻页以免遗漏更早商品的变更。
                    if (
                        stop_when_page_all_existing
                        and page_all_existing
                        and page_changed_count == 0
                        and (
                            not normalized_required_title_keyword
                            or matched_required_title_keyword
                        )
                    ):
                        logger.info(
                            f"账号[{account.account_id}]「{group_name}」命中整页已存在且无字段变更，停止继续获取后续页面"
                        )
                        early_break = True
                        break

                    if len(items) < page_size:
                        logger.info(
                            f"账号[{account.account_id}]「{group_name}」第 {page_number} 页数量少于页大小，结束该分组"
                        )
                        break

                    page_number += 1
                    await asyncio.sleep(1)

            # 仅当两个分组都完整翻页抓完（未受 max_pages 截断、未因「整页已存在」提前停止）
            # 才允许对账：增量同步只抓了前几页，此时 fetched_ids 不完整，
            # 对账会把其余本地商品误判为已下架/删除（详情判定失败还会误标 unknown）
            groups_complete = (
                len(target_groups) >= 2
                and not early_break
                and not (max_pages and fetched_pages >= max_pages)
            )
        except Exception as exc:
            return {"success": False, "message": f"获取商品失败: {exc}"}
        finally:
            await manager.close()

        # 对账：本地存在但本次未返回的商品，通过详情接口判定状态（下架/删除/未知）
        fetched_ids = {
            str(it.get("id") or "").strip()
            for it in fetched_items
            if str(it.get("id") or "").strip()
        }
        on_sale_fetched = sum(
            1 for it in fetched_items if it.get("item_status") == 0
        )
        if groups_complete and fetched_ids and on_sale_fetched > 0:
            # 抓取到 0 件商品时不做对账（接口抖动/被限流时会误判全部本地商品）
            try:
                await self._reconcile_item_statuses(account, fetched_ids)
            except Exception as exc:
                logger.warning(
                    f"账号[{account.account_id}] 商品状态对账失败（不影响商品同步）: {exc}"
                )

        # 新商品入库后自动关联卡券（按前缀编号匹配；失败不影响商品同步主流程）
        try:
            await self._auto_match_cards_after_fetch(account, fetched_items)
        except Exception as exc:
            logger.warning(
                f"账号[{account.account_id}] 入库后自动关联卡券失败（不影响商品同步）: {exc}"
            )

        return {
            "success": True,
            "message": f"获取到 {len(fetched_items)} 个商品",
            "items": fetched_items,
            "total_count": len(fetched_items),
            "total_pages": fetched_pages,
            "page_size": page_size,
            "saved_count": total_saved_count,
        }

    async def _auto_match_cards_after_fetch(
        self, account: XYAccount, fetched_items: list[dict]
    ) -> None:
        """商品入库后自动按前缀编号关联卡券（账号级开关 auto_match_cards 控制，默认开启）。

        使用独立 session；任何异常向上抛出，由调用方捕获并记录，绝不影响商品同步主流程。
        """
        from common.db.session import async_session_maker
        from common.services.card_matcher import CardMatcher

        if not getattr(account, "auto_match_cards", True):
            return  # 该账号已关闭自动关联

        item_ids = [
            str(it.get("id") or "").strip()
            for it in fetched_items
            if str(it.get("id") or "").strip()
        ]
        if not item_ids:
            return

        async with async_session_maker() as match_session:
            matcher = CardMatcher(match_session)
            stats = await matcher.match_cards_for_item_ids(account.owner_id, item_ids)
            if stats.get("added"):
                logger.info(
                    f"[自动关联卡券] 账号 {account.account_id} 本次入库 {len(item_ids)} 件，"
                    f"新增关联 {stats['added']} 对（匹配卡券 {stats['matched_cards']} 张）"
                )

    async def _reconcile_item_statuses(self, account: XYAccount, fetched_ids: set) -> None:
        """完整抓取后对账：本地存在但本次未返回的商品，通过详情接口判定状态。

        - 已标记 offline/deleted 的商品不重复判定（下架/删除商品不会再出现在分组列表里）
        - 节流约 1 秒/个；详情接口临时失败（网络异常等）跳过该商品，防止误标
        - 判定结果写入 metadata_json.item_status：offline/deleted/unknown（字符串语义值）
        """
        from common.db.session import async_session_maker
        from common.services.xianyu_detail_client import XianyuItemDetailClient

        async with async_session_maker() as session:
            local_rows = list(
                (
                    await session.execute(
                        select(XYCatalogItem).where(
                            XYCatalogItem.owner_id == account.owner_id,
                            XYCatalogItem.account_pk == account.id,
                        )
                    )
                ).scalars().all()
            )

        pending = [
            row
            for row in local_rows
            if row.item_id not in fetched_ids
            and (row.metadata_json or {}).get("item_status")
            not in ("offline", "deleted", "inactive")
        ]
        if not pending:
            return

        logger.info(
            f"[商品状态对账] 账号 {account.account_id}：{len(pending)} 个本地商品未在列表中，逐个判定状态"
        )

        client = XianyuItemDetailClient(
            account.account_id, account.cookie, owner_id=account.owner_id
        )

        # 限流哨兵：详情接口的 FAIL_SYS_USER_VALIDATE 既可能是商品级（久置失效），
        # 也可能是请求级（账号被限流时对一切商品都返回）。先探测一个确定在售的商品，
        # 若在售商品也被拒绝，说明当前会话不可用，放弃本轮对账，防止整批误标。
        canary_id = next(iter(fetched_ids), None)
        if canary_id:
            try:
                canary = await client.get_detail(canary_id)
            except Exception as exc:
                logger.warning(
                    f"[商品状态对账] 哨兵探测异常，放弃本轮对账: {exc}"
                )
                return
            if not canary.get("success"):
                logger.warning(
                    f"[商品状态对账] 详情接口当前不可用（在售商品 {canary_id} 返回 "
                    f"{str(canary.get('error'))[:60]}），疑似限流，放弃本轮对账"
                )
                return

        async with async_session_maker() as session:
            for row in pending:
                try:
                    detail = await client.get_detail(row.item_id)
                    error_text = str(detail.get("error") or "")
                    if detail.get("success"):
                        item_do = (detail.get("detail") or {}).get("itemDO") or {}
                        status = item_do.get("itemStatus")
                        # -2=已下架（主动下架）；其他非预期值一律标未知
                        new_status = "offline" if status == -2 else "unknown"
                    elif "DEL_NOT_FOUND" in error_text:
                        new_status = "deleted"
                    elif "USER_VALIDATE" in error_text:
                        # 哨兵已验证会话可用，此错误为商品级：久置的下架/删除无法再区分，标记失效
                        new_status = "inactive"
                    else:
                        # 临时失败（网络异常/超时）：跳过，下一轮再判定
                        continue
                except Exception as exc:
                    logger.warning(
                        f"[商品状态对账] 商品 {row.item_id} 详情判定异常: {exc}"
                    )
                    continue

                obj = (
                    await session.execute(
                        select(XYCatalogItem).where(XYCatalogItem.id == row.id)
                    )
                ).scalar_one_or_none()
                if obj is None:
                    continue
                meta = dict(obj.metadata_json or {})
                if meta.get("item_status") == new_status:
                    continue
                meta["item_status"] = new_status
                # 退场状态补 offline_at（权重恢复信号起点）
                if new_status in ("offline", "deleted", "inactive") and not meta.get("offline_at"):
                    from common.utils.time_utils import get_beijing_now

                    meta["offline_at"] = get_beijing_now().isoformat()
                obj.metadata_json = meta
                flag_modified(obj, "metadata_json")
                await session.commit()
                logger.info(
                    f"[商品状态对账] 商品 {row.item_id} 状态标记为 {new_status}"
                )
                await asyncio.sleep(1.5)

    async def fetch_all_items_from_accounts(
        self,
        accounts: list[XYAccount],
        page_size: int = 20,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        """按账号列表批量抓取全部商品并汇总结果"""
        if not accounts:
            return {
                "success": False,
                "message": "当前范围内没有可获取商品的账号",
                "account_count": 0,
                "success_account_count": 0,
                "failed_account_count": 0,
                "total_count": 0,
                "saved_count": 0,
                "failed_accounts": [],
                "results": [],
            }

        account_results: list[dict[str, Any]] = []
        failed_accounts: list[str] = []
        total_count = 0
        saved_count = 0
        success_account_count = 0

        for account in accounts:
            try:
                result = await self.fetch_all_items_from_account(
                    account=account,
                    page_size=page_size,
                    max_pages=max_pages,
                )
                account_success = bool(result.get("success"))
                account_total_count = int(result.get("total_count") or 0)
                account_saved_count = int(result.get("saved_count") or 0)
                account_message = str(result.get("message") or "")
            except Exception as exc:
                await self.session.rollback()
                account_success = False
                account_total_count = 0
                account_saved_count = 0
                account_message = f"获取商品失败: {exc}"

            if account_success:
                success_account_count += 1
                total_count += account_total_count
                saved_count += account_saved_count
            else:
                failed_accounts.append(f"{account.account_id}: {account_message or '获取商品失败'}")

            account_results.append(
                {
                    "cookie_id": account.account_id,
                    "success": account_success,
                    "message": account_message,
                    "total_count": account_total_count,
                    "saved_count": account_saved_count,
                }
            )

        failed_account_count = len(accounts) - success_account_count
        if success_account_count == 0:
            message = f"获取所有账号商品失败，共 {failed_account_count} 个账号执行失败"
            success = False
        elif failed_account_count == 0:
            message = f"成功获取 {success_account_count} 个账号商品，共 {total_count} 件，保存 {saved_count} 件"
            success = True
        else:
            message = f"已获取 {success_account_count} 个账号商品，共 {total_count} 件，保存 {saved_count} 件；失败 {failed_account_count} 个账号"
            success = True

        return {
            "success": success,
            "message": message,
            "account_count": len(accounts),
            "success_account_count": success_account_count,
            "failed_account_count": failed_account_count,
            "total_count": total_count,
            "saved_count": saved_count,
            "failed_accounts": failed_accounts,
            "results": account_results,
        }

    async def save_fetched_items(
        self,
        account: XYAccount,
        items: list[dict],
    ) -> tuple[int, int]:
        """保存抓取到的商品数据到本地库（逐个商品独立提交）

        返回 (保存成功的商品数, 有实际字段变更的商品数)。
        """
        valid_items, _ = self._collect_valid_item_entries(items)
        if not valid_items:
            return 0, 0

        saved_count = 0
        changed_count = 0
        for item_id, item in valid_items:
            success, has_changes = await self._save_single_item(account, item_id, item)
            if success:
                saved_count += 1
            if has_changes:
                changed_count += 1

        return saved_count, changed_count

    async def _save_single_item(
        self,
        account: XYAccount,
        item_id: str,
        item: dict,
    ) -> tuple[bool, bool]:
        """保存单个商品并独立提交（更新或新增）。

        返回 (是否保存成功, 是否有实际字段变更)；
        单个商品失败只回滚自身，不抛出异常，由调用方继续处理其余商品。
        """
        try:
            has_changes = await self._apply_single_item(account, item_id, item)
            if has_changes:
                await self.session.commit()
            return True, has_changes
        except IntegrityError:
            await self.session.rollback()
            logger.info(
                f"账号[{account.account_id}]商品 {item_id} 保存命中唯一约束，转为更新已存在记录后重试"
            )
            try:
                has_changes = await self._apply_single_item(account, item_id, item)
                if has_changes:
                    await self.session.commit()
                return True, has_changes
            except Exception as exc:
                await self.session.rollback()
                logger.warning(
                    f"账号[{account.account_id}]商品 {item_id} 重试更新仍失败，跳过该商品: {exc}"
                )
                return False, False
        except Exception as exc:
            await self.session.rollback()
            logger.warning(
                f"账号[{account.account_id}]商品 {item_id} 保存失败，跳过该商品: {exc}"
            )
            return False, False

    async def _apply_single_item(
        self,
        account: XYAccount,
        item_id: str,
        item: dict,
    ) -> bool:
        """将单个商品写入会话（更新或新增），不提交。

        每次都在当前事务内实时查询已存在记录，保证拿到的是当前事务可用的对象。
        返回 True 表示有实际变更（新增或字段值变化），False 表示无需更新。
        """
        category = str(item.get("category_id", ""))

        stmt = select(XYCatalogItem).where(
            XYCatalogItem.owner_id == account.owner_id,
            XYCatalogItem.account_pk == account.id,
            XYCatalogItem.item_id == item_id,
        )
        existing_item = (await self.session.execute(stmt)).scalars().first()

        if existing_item:
            new_title = item.get("title", "")
            new_price = item.get("price_text", "")
            normalized_pt = normalize_publish_time(item.get("publish_time"))
            raw_status = item.get("item_status")
            if raw_status is not None and str(raw_status) not in ("", "None"):
                try:
                    normalized_status = int(raw_status)
                except (ValueError, TypeError):
                    normalized_status = None
            else:
                normalized_status = None
            changed = False
            if existing_item.title != new_title:
                existing_item.title = new_title
                changed = True
            if existing_item.price != new_price:
                existing_item.price = new_price
                changed = True
            metadata_json = existing_item.metadata_json or {}
            if metadata_json.get("category") != category:
                metadata_json["category"] = category
                changed = True
            # 补存上架时间（供自动下架规则筛选）
            if normalized_pt and metadata_json.get("publish_time") != normalized_pt:
                metadata_json["publish_time"] = normalized_pt
                changed = True
            # 刷新商品状态（0=在售, 1=已售出；对账写入的 offline/deleted/unknown 仅在列表返回时被覆盖）
            if (
                normalized_status is not None
                and metadata_json.get("item_status") != normalized_status
            ):
                metadata_json["item_status"] = normalized_status
                changed = True
            if changed:
                existing_item.metadata_json = metadata_json
                flag_modified(existing_item, "metadata_json")
            return changed

        raw_status = item.get("item_status")
        try:
            normalized_status = int(raw_status) if raw_status is not None and str(raw_status) not in ("", "None") else None
        except (ValueError, TypeError):
            normalized_status = None

        new_item = XYCatalogItem(
            owner_id=account.owner_id,
            account_pk=account.id,
            item_id=item_id,
            title=item.get("title", ""),
            price=item.get("price_text", ""),
            is_polished=False,
            metadata_json={
                "description": "",
                "category": category,
                "publish_time": normalize_publish_time(item.get("publish_time")),
                "item_status": normalized_status,
                "detail": json.dumps(item, ensure_ascii=False),
            },
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(new_item)
        return True
    
    async def _get_default_reply_status_batch(self, items_data: list) -> Dict[tuple, dict]:
        """批量获取商品默认回复状态
        
        Args:
            items_data: [(item, account_id), ...] 商品数据列表
            
        Returns:
            {(account_id, item_id): {'enabled': bool, 'has_config': bool}, ...}
        """
        if not items_data:
            return {}
        
        # 收集所有需要查询的 (account_id, item_id) 组合
        item_keys = [(acct_id, item.item_id) for item, acct_id in items_data]
        account_ids = list(set(acct_id for acct_id, _ in item_keys))
        item_ids = list(set(item_id for _, item_id in item_keys))
        
        # 查询所有相关的默认回复配置
        stmt = select(DefaultReply).where(
            DefaultReply.account_id.in_(account_ids),
            DefaultReply.item_id.in_(item_ids)
        )
        result = await self.session.execute(stmt)
        replies = result.scalars().all()
        
        # 构建映射
        reply_map = {}
        for reply in replies:
            key = (reply.account_id, reply.item_id)
            reply_map[key] = {
                'enabled': reply.enabled,
                'has_config': True
            }
        
        return reply_map

    async def _get_card_status_batch(self, items_data: list) -> Set[str]:
        """批量获取商品卡券配置状态（通过关联表+旧字段兼容，不区分用户）
        
        Args:
            items_data: [(item, account_id), ...] 商品数据列表
            
        Returns:
            {item_id, ...} 已配置卡券的商品ID集合
        """
        if not items_data:
            return set()
        
        # 收集所有需要查询的 item_id
        item_ids = list(set(item.item_id for item, _ in items_data))
        
        from common.services.card_matcher import CardMatcher
        matcher = CardMatcher(self.session)
        
        # 按 item_id 查询卡券状态（不区分用户，与发货配置弹窗逻辑一致）
        status_map = await matcher.get_items_with_card_status(item_ids)
        configured_items: Set[str] = set()
        for item_id, has_card in status_map.items():
            if has_card:
                configured_items.add(item_id)
        
        return configured_items

    async def get_item(self, owner_id: int | None, account_id: str, item_id: str) -> dict | None:
        stmt = (
            select(XYCatalogItem)
            .join(XYAccount, XYCatalogItem.account_pk == XYAccount.id)
            .where(
                XYAccount.account_id == account_id,
                XYCatalogItem.item_id == item_id,
            )
        )
        # 管理员 owner_id 为 None，不限制所有者
        if owner_id is not None:
            stmt = stmt.where(XYCatalogItem.owner_id == owner_id)
        result = await self.session.execute(stmt)
        item = result.scalars().first()
        if not item:
            return None
        return self._serialize_item(item, account_id)

    async def update_item(self, account: XYAccount, item_id: str, data: dict) -> bool:
        """更新商品信息"""
        from sqlalchemy.orm.attributes import flag_modified
        from loguru import logger
        
        logger.info(f"ItemService.update_item: item_id={item_id}, data={data}")
        
        stmt = (
            select(XYCatalogItem)
            .where(
                XYCatalogItem.owner_id == account.owner_id,
                XYCatalogItem.account_pk == account.id,
                XYCatalogItem.item_id == item_id,
            )
        )
        result = await self.session.execute(stmt)
        item = result.scalars().first()
        if not item:
            logger.warning(f"商品不存在: item_id={item_id}")
            return False
        
        logger.info(f"找到商品: id={item.id}, title={item.title}, metadata={item.metadata_json}")
        
        # 字段名映射（前端使用item_前缀，数据库metadata中不使用前缀）
        field_mapping = {
            'item_detail': 'detail',
            'item_description': 'description',
            'item_category': 'category',
            'item_title': 'title',
            'item_price': 'price',
        }
        
        # 更新字段
        metadata_modified = False
        for key, value in data.items():
            # 检查是否是直接字段（title, price, ai_prompt等）
            if key in ['title', 'price', 'ai_prompt'] and hasattr(item, key):
                logger.info(f"更新字段 {key}: {getattr(item, key)} -> {value}")
                setattr(item, key, value)
            # 检查是否需要映射到metadata
            elif key in field_mapping:
                mapped_key = field_mapping[key]
                if item.metadata_json is None:
                    item.metadata_json = {}
                logger.info(f"更新metadata字段 {key} -> {mapped_key}: {item.metadata_json.get(mapped_key)} -> {value}")
                item.metadata_json[mapped_key] = value
                metadata_modified = True
            # 其他字段直接存储到metadata
            elif item.metadata_json is not None:
                logger.info(f"更新metadata字段 {key}: {item.metadata_json.get(key)} -> {value}")
                item.metadata_json[key] = value
                metadata_modified = True
        
        # 标记metadata_json已修改（SQLAlchemy不会自动检测JSON字段的变化）
        if metadata_modified:
            logger.info("标记metadata_json已修改")
            flag_modified(item, 'metadata_json')
        
        await self.session.commit()
        logger.info(f"商品更新已提交: item_id={item_id}")
        return True

    async def delete_item(self, account: XYAccount, item_id: str) -> bool:
        """删除商品（软标记：item_status=deleted + deleted_at，保留记录供权重恢复信号）。

        商品记录不再物理删除；卡券关联同步解除。
        """
        from loguru import logger
        from common.services.card_matcher import CardMatcher

        stmt = (
            select(XYCatalogItem)
            .where(
                XYCatalogItem.owner_id == account.owner_id,
                XYCatalogItem.account_pk == account.id,
                XYCatalogItem.item_id == item_id,
            )
        )
        result = await self.session.execute(stmt)
        item = result.scalars().first()
        if not item:
            return False

        # 级联删除关联表记录
        matcher = CardMatcher(self.session)
        rel_count = await matcher.delete_relations_by_item_id(item_id)
        if rel_count > 0:
            logger.info(f"删除商品 {item_id} 的 {rel_count} 条卡券关联记录")

        meta = dict(item.metadata_json or {})
        meta["item_status"] = "deleted"
        # 与下架/对账的 offline_at 信号统一用北京时间（权重恢复按天计算，避免时区偏移跨天误差）
        from common.utils.time_utils import get_beijing_now

        meta["deleted_at"] = get_beijing_now().isoformat()
        item.metadata_json = meta
        flag_modified(item, "metadata_json")
        await self.session.commit()
        return True

    async def delete_item_smart(
        self, owner_id: int | None, item_id: str, account: XYAccount | None = None
    ) -> str:
        """统一删除商品，兼容账号已被删除的孤儿商品。

        删除规则（与前端约定一致）：
        - 传入 account（调用方已校验账号归属）：按 (owner_id, account.id, item_id) 精确删除；
        - 未传 account：在 owner 范围内按 item_id 定位商品，
            * 若其所属账号仍存在 → 返回 'account_required'，要求调用方指定账号后再删；
            * 若所属账号已不存在（孤儿商品）→ 直接按 item_id 删除并清理卡券关联。

        Args:
            owner_id: 用户ID（管理员场景可为 None，表示不限制归属）
            item_id: 商品ID
            account: 已校验的账号对象（可选）

        Returns:
            'ok'：删除成功；'not_found'：商品不存在；'account_required'：商品所属账号仍存在，需指定账号
        """
        # CardMatcher 采用局部导入，避免与 card_matcher 模块产生循环依赖（与 delete_item 保持一致）
        from common.services.card_matcher import CardMatcher

        # 情况一：调用方已指定并校验账号 → 复用原有按账号删除逻辑
        if account is not None:
            ok = await self.delete_item(account, item_id)
            return "ok" if ok else "not_found"

        # 情况二：未指定账号 → 按 owner + item_id 定位商品记录
        stmt = select(XYCatalogItem).where(XYCatalogItem.item_id == item_id)
        if owner_id is not None:
            stmt = stmt.where(XYCatalogItem.owner_id == owner_id)
        items = (await self.session.execute(stmt)).scalars().all()
        if not items:
            return "not_found"

        # 校验这些商品所属账号是否仍然存在
        account_pks = {it.account_pk for it in items}
        existing_rows = await self.session.execute(
            select(XYAccount.id).where(XYAccount.id.in_(account_pks))
        )
        existing_pks = {row[0] for row in existing_rows.all()}
        if existing_pks:
            # 商品所属账号仍存在 → 不允许脱离账号删除，要求指定账号
            return "account_required"

        # 全部为孤儿商品（账号已删除）→ 按 item_id 删除，并清理卡券关联
        matcher = CardMatcher(self.session)
        rel_count = await matcher.delete_relations_by_item_id(item_id)
        if rel_count > 0:
            logger.info(f"删除孤儿商品 {item_id} 的 {rel_count} 条卡券关联记录")
        for it in items:
            await self.session.delete(it)
        await self.session.commit()
        logger.info(f"已删除孤儿商品 {item_id}（所属账号已不存在），共 {len(items)} 条记录")
        return "ok"

    async def delete_many(self, account: XYAccount, item_ids: list[str]) -> int:
        deleted = 0
        for item_id in item_ids:
            success = await self.delete_item(account, item_id)
            if success:
                deleted += 1
        return deleted

    def _serialize_item(self, item: XYCatalogItem, account_id: str, default_reply_info: dict | None = None, has_card: bool = False) -> dict:
        metadata = item.metadata_json or {}
        return {
            "id": item.id,
            "cookie_id": account_id,
            "item_id": item.item_id,
            "title": item.title,
            "item_title": item.title,
            "item_status": _normalize_item_status(metadata.get("item_status")),
            "item_description": metadata.get("description"),
            "item_detail": metadata.get("detail"),
            "item_category": metadata.get("category"),
            "item_price": item.price,
            "ai_prompt": item.ai_prompt or "",
            "has_ai_prompt": bool(item.ai_prompt),
            "is_polished": item.is_polished or False,
            "is_multi_spec": metadata.get("is_multi_spec", False),
            "multi_quantity_delivery": metadata.get("multi_quantity_delivery", False),
            "default_reply_enabled": default_reply_info.get("enabled", False) if default_reply_info else False,
            "has_default_reply": default_reply_info.get("has_config", False) if default_reply_info else False,
            "has_card": has_card,
            "created_at": self._format_dt(item.created_at),
            "updated_at": self._format_dt(item.updated_at),
        }

    @staticmethod
    def _format_dt(value: datetime | str | None) -> str | None:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            return value
        return None
