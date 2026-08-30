"""
商品运营指标采集服务

功能（scheduler 商品指标快照任务使用）：
1. 调数据罗盘商品接口（datacompass.item.list）批量采集在售商品的
   当日（recent1d）与近7天（recent7d）曝光/浏览/咨询/成交指标
2. 逐件调闲鱼商品详情接口（mtop.taobao.idle.awesome.detail）采集累计想要数
3. UPSERT 到 xy_item_stats_daily 快照表（唯一键兜底，幂等）
4. 按保留天数清理过期快照（系统设置 item_stats.retention_days，默认 30 天）

口径说明（实调验证）：
- recent1d / recent7d 为滚动窗口；want_count 为累计值；上架天数为当前状态值
- datacompass.item.list 仅覆盖在售商品，pageSize=300 一次返回全量
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.xy_account import XYAccount
from common.services.xianyu_mtop import mtop_call
from common.utils.time_utils import get_beijing_now

# 数据罗盘商品列表接口（一次性全量在售商品，pageSize=300）
DATACOMPASS_ITEM_LIST_API = "mtop.alibaba.idle.seller.pc.datacompass.item.list"
# 闲鱼 H5 商品详情接口（累计想要数）
ITEM_DETAIL_API = "mtop.taobao.idle.awesome.detail"

# 快照保留天数默认值与系统设置键
DEFAULT_RETENTION_DAYS = 30
RETENTION_SETTING_KEY = "item_stats.retention_days"
RETENTION_MIN_DAYS = 7
RETENTION_MAX_DAYS = 365

# 详情接口并发数
DETAIL_CONCURRENCY = 5


async def _read_retention_days(session: AsyncSession) -> int:
    """从系统设置读取快照保留天数，默认 30 天（范围 7~365）"""
    try:
        result = await session.execute(
            text("SELECT value FROM xy_system_settings WHERE `key` = :key LIMIT 1"),
            {"key": RETENTION_SETTING_KEY},
        )
        row = result.fetchone()
        if row and row[0] not in (None, ""):
            days = int(str(row[0]).strip())
            if RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS:
                return days
            logger.warning(
                f"商品指标快照保留天数 {days} 不在有效范围({RETENTION_MIN_DAYS}~{RETENTION_MAX_DAYS})，使用默认值"
            )
    except Exception as e:
        logger.warning(f"读取商品指标快照保留天数配置失败，使用默认值: {e}")
    return DEFAULT_RETENTION_DAYS


async def fetch_item_stats_list(
    cookies_str: str,
    seller_id: str,
    date_type: str,
) -> Optional[List[Dict[str, Any]]]:
    """批量获取在售商品指标列表（数据罗盘商品接口）

    Args:
        cookies_str: 账号 Cookie
        seller_id: 闲鱼卖家ID（selectedSellerId）
        date_type: recent1d / recent7d

    Returns:
        商品指标行列表，失败返回 None
    """
    result = await mtop_call(
        seller_id,
        cookies_str,
        DATACOMPASS_ITEM_LIST_API,
        "1.0",
        {
            "selectedSellerId": seller_id,
            "dateType": date_type,
            "page": 1,
            "pageSize": 300,
        },
        # 数据罗盘是卖家工作台专属接口，需要 COMMONPRO 站点上下文请求头（实调验证，缺失时返回无权限）
        extra_headers={"idle_site_biz_code": "COMMONPRO", "idle_user_group_member_id": ""},
        referer="https://seller.goofish.com/?site=COMMONPRO",
    )
    if not result.get("success"):
        logger.warning(f"【{seller_id}】商品指标列表获取失败({date_type}): {result.get('error')}")
        return None
    data = (result.get("res") or {}).get("data") or {}
    inner = data.get("data") or {}
    rows = inner.get("list") or []
    logger.info(f"【{seller_id}】商品指标列表({date_type})获取成功，共 {len(rows)} 件")
    return rows


async def fetch_item_want_count(cookies_str: str, seller_id: str, item_id: str) -> Optional[int]:
    """获取单件商品的累计想要数（商品详情接口），失败返回 None"""
    result = await mtop_call(
        seller_id,
        cookies_str,
        ITEM_DETAIL_API,
        "1.0",
        {"itemId": str(item_id)},
    )
    if not result.get("success"):
        return None
    data = (result.get("res") or {}).get("data") or {}
    item_do = data.get("itemDO") or {}
    want = item_do.get("wantCnt")
    return int(want) if isinstance(want, (int, float, str)) and str(want).isdigit() else None


def _to_int(value: Any) -> Optional[int]:
    """安全转 int（字符串数字/数字均可），失败返回 None"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> Optional[str]:
    """安全转短字符串"""
    if value is None:
        return None
    s = str(value)
    return s[:32] if s else None


async def snapshot_account_stats(
    session: AsyncSession,
    account: XYAccount,
    stat_date: str,
) -> Dict[str, Any]:
    """采集单个账号的商品运营指标并写入快照表

    Args:
        session: 数据库会话
        account: 闲鱼账号（需含有效 Cookie）
        stat_date: 快照日期 yyyyMMdd（采集日期）

    Returns:
        执行结果统计 {"success": bool, "item_count": int, "want_ok": int, "error": str}
    """
    seller_id = account.account_id
    cookie = account.cookie

    # 1. 批量采集两个窗口的指标
    rows_1d = await fetch_item_stats_list(cookie, seller_id, "recent1d")
    rows_7d = await fetch_item_stats_list(cookie, seller_id, "recent7d")
    if rows_1d is None or rows_7d is None:
        return {"success": False, "item_count": 0, "want_ok": 0, "error": "数据罗盘商品列表获取失败"}

    map_7d: Dict[str, Dict[str, Any]] = {str(r.get("itmId")): r for r in rows_7d if r.get("itmId")}
    items = [r for r in rows_1d if r.get("itmId")]

    # 2. 逐件采集想要数（并发受控，失败置 None 不中断）
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def fetch_one(item_id: str) -> Optional[int]:
        async with semaphore:
            return await fetch_item_want_count(cookie, seller_id, item_id)

    want_results = await asyncio.gather(
        *[fetch_one(str(r.get("itmId"))) for r in items],
        return_exceptions=True,
    )

    # 3. UPSERT 快照表
    want_ok = 0
    inserted = 0
    upsert_sql = text(
        """
        INSERT INTO xy_item_stats_daily
            (account_id, item_id, stat_date,
             show_pv_1d, show_uv_1d, ipv_1d, ipv_uv_1d, chat_uv_1d,
             pay_ord_cnt_1d, pay_byr_cnt_1d, pay_amt_1d, ipv_pay_ucvr_1d,
             show_pv_7d, show_uv_7d, ipv_7d, ipv_uv_7d, chat_uv_7d,
             pay_ord_cnt_7d, pay_byr_cnt_7d, pay_amt_7d, ipv_pay_ucvr_7d,
             want_count, days_on_shelf, post_dt, created_at, updated_at)
        VALUES
            (:account_id, :item_id, :stat_date,
             :show_pv_1d, :show_uv_1d, :ipv_1d, :ipv_uv_1d, :chat_uv_1d,
             :pay_ord_cnt_1d, :pay_byr_cnt_1d, :pay_amt_1d, :ipv_pay_ucvr_1d,
             :show_pv_7d, :show_uv_7d, :ipv_7d, :ipv_uv_7d, :chat_uv_7d,
             :pay_ord_cnt_7d, :pay_byr_cnt_7d, :pay_amt_7d, :ipv_pay_ucvr_7d,
             :want_count, :days_on_shelf, :post_dt, NOW(), NOW())
        AS new
        ON DUPLICATE KEY UPDATE
             show_pv_1d = new.show_pv_1d, show_uv_1d = new.show_uv_1d,
             ipv_1d = new.ipv_1d, ipv_uv_1d = new.ipv_uv_1d,
             chat_uv_1d = new.chat_uv_1d,
             pay_ord_cnt_1d = new.pay_ord_cnt_1d, pay_byr_cnt_1d = new.pay_byr_cnt_1d,
             pay_amt_1d = new.pay_amt_1d, ipv_pay_ucvr_1d = new.ipv_pay_ucvr_1d,
             show_pv_7d = new.show_pv_7d, show_uv_7d = new.show_uv_7d,
             ipv_7d = new.ipv_7d, ipv_uv_7d = new.ipv_uv_7d,
             chat_uv_7d = new.chat_uv_7d,
             pay_ord_cnt_7d = new.pay_ord_cnt_7d, pay_byr_cnt_7d = new.pay_byr_cnt_7d,
             pay_amt_7d = new.pay_amt_7d, ipv_pay_ucvr_7d = new.ipv_pay_ucvr_7d,
             want_count = IF(new.want_count IS NULL, want_count, new.want_count),
             days_on_shelf = new.days_on_shelf, post_dt = new.post_dt,
             updated_at = NOW()
        """
    )

    for row, want in zip(items, want_results):
        item_id = str(row.get("itmId"))
        want_count = want if isinstance(want, int) else None
        if want_count is not None:
            want_ok += 1
        row_7d = map_7d.get(item_id) or {}

        params = {
            "account_id": seller_id,
            "item_id": item_id,
            "stat_date": stat_date,
            "show_pv_1d": _to_int(row.get("showPv")),
            "show_uv_1d": _to_int(row.get("showUv")),
            "ipv_1d": _to_int(row.get("ipv")),
            "ipv_uv_1d": _to_int(row.get("ipvUv")),
            "chat_uv_1d": _to_int(row.get("chatUv")),
            "pay_ord_cnt_1d": _to_int(row.get("payOrdCnt")),
            "pay_byr_cnt_1d": _to_int(row.get("payByrCnt")),
            "pay_amt_1d": _to_str(row.get("payAmt")),
            "ipv_pay_ucvr_1d": _to_str(row.get("ipvPayUcvr")),
            "show_pv_7d": _to_int(row_7d.get("showPv")),
            "show_uv_7d": _to_int(row_7d.get("showUv")),
            "ipv_7d": _to_int(row_7d.get("ipv")),
            "ipv_uv_7d": _to_int(row_7d.get("ipvUv")),
            "chat_uv_7d": _to_int(row_7d.get("chatUv")),
            "pay_ord_cnt_7d": _to_int(row_7d.get("payOrdCnt")),
            "pay_byr_cnt_7d": _to_int(row_7d.get("payByrCnt")),
            "pay_amt_7d": _to_str(row_7d.get("payAmt")),
            "ipv_pay_ucvr_7d": _to_str(row_7d.get("ipvPayUcvr")),
            "want_count": want_count,
            "days_on_shelf": _to_int(row.get("daysOnShelf")),
            "post_dt": _to_str(row.get("postDt")),
        }
        try:
            await session.execute(upsert_sql, params)
            inserted += 1
        except Exception as e:
            logger.warning(f"【{seller_id}】商品 {item_id} 快照写入失败: {e}")

    await session.commit()
    logger.info(
        f"【{seller_id}】商品指标快照完成：写入 {inserted}/{len(items)} 件，"
        f"想要数成功 {want_ok} 件（stat_date={stat_date}）"
    )
    return {"success": True, "item_count": inserted, "want_ok": want_ok, "error": ""}


async def cleanup_expired_snapshots(session: AsyncSession) -> int:
    """按保留天数清理过期快照，返回删除行数"""
    retention_days = await _read_retention_days(session)
    now = get_beijing_now()
    from datetime import timedelta

    cutoff = (now - timedelta(days=retention_days)).strftime("%Y%m%d")
    result = await session.execute(
        text("DELETE FROM xy_item_stats_daily WHERE stat_date < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.commit()
    if result.rowcount:
        logger.info(
            f"商品指标快照清理完成：删除 {result.rowcount} 行（早于 {cutoff}，保留 {retention_days} 天）"
        )
    return result.rowcount


async def has_today_snapshot(session: AsyncSession, account_id: str, stat_date: str) -> bool:
    """检查账号当天是否已有快照（幂等保护，scheduler 重启不重复采集）"""
    result = await session.execute(
        text(
            "SELECT 1 FROM xy_item_stats_daily "
            "WHERE account_id = :account_id AND stat_date = :stat_date LIMIT 1"
        ),
        {"account_id": account_id, "stat_date": stat_date},
    )
    return result.fetchone() is not None
