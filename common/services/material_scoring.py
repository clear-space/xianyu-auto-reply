"""
商品素材权重计算服务（上架权重算法）

功能：
1. 按编号采集素材的历史信号：发布生命周期信号（首次使用/下架/删除/发布失败）
   为系统数据；市场表现信号（曝光/浏览/咨询/成交/转化率/想要）取自闲鱼官方
   快照表 xy_item_stats_daily（按标题前缀编号聚合该素材发布出的商品）
2. 官方信号在素材池内先归一化（percentile/log），再乘以权重计入总分
3. 供定时发布随机模式的加权选料使用（管理员定义的算法参数驱动）

权重公式（无阈值，归一化加权，方向：表现越好越该发）：
    weight = max(1,
      100 基础分
      + first_use_bonus × [首次使用]                    # 系统发布历史
      + w_exposure × p(近7天曝光合计)                   # 官方，正向
      + w_browse   × p(近7天浏览合计)
      + w_chat     × p(近7天咨询合计)
      + w_sale     × p(近7天成交合计)
      + w_ucvr     × p(近7天转化率)
      + w_want     × p(累计想要合计)
      + w_sold     × [本地已售出]                       # 系统状态，好信号
      − max(0, 100 − 下架天数 × offline_recover_per_day)
      − max(0, 100 − 删除天数 × deleted_recover_per_day)
      − fail_penalty × 近60天发布失败次数
      保底 1 分（永不彻底排除）
    )

无编号/无官方数据的素材：官方信号按中性 0.5 计，靠 first_use/sold 等系统信号决定。

兼容：旧版参数（recent_order_bonus/sold_bonus）自动映射为新权重
（订单信号切换为官方近7天成交），其余新权重置 0 中性。
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select

# 素材标题前缀编号：一个字母 + 三位数字（与一键关联卡券共用同一规则）
_PREFIX_NUMBER_RE = re.compile(r"^([A-Za-z])(\d{3})(?!\d)")

# 系统默认权重参数（规则未选择算法时使用）
DEFAULT_WEIGHT_PARAMS: Dict[str, Any] = {
    "first_use_bonus": 50,
    "w_exposure": 40,
    "w_browse": 20,
    "w_chat": 20,
    "w_sale": 60,
    "w_ucvr": 20,
    "w_want": 30,
    "w_sold": 25,
    "offline_recover_per_day": 2,
    "deleted_recover_per_day": 1,
    "fail_penalty": 10,
    "exclude_sold": False,
    # 选料方式：weighted-加权随机（权重=概率）；top-按权重直选（高分必先选）
    "sample_mode": "weighted",
    # 归一化方式：percentile-素材池内百分位；log-对数归一化
    "norm_method": "percentile",
}

# 选料方式/归一化合法值
SAMPLE_MODES = ("weighted", "top")
NORM_METHODS = ("percentile", "log")

# 新版权重参数键
_WEIGHT_KEYS = ("w_exposure", "w_browse", "w_chat", "w_sale", "w_ucvr", "w_want", "w_sold")

# 同编号多条商品记录的状态聚合优先级（在售 > 已售出 > 下架 > 删除 > 失效 > 未知）
_STATUS_PRIORITY = {"on_sale": 0, "sold": 1, "offline": 2, "deleted": 3, "inactive": 4, "unknown": 5}


def extract_prefix_number(text: Optional[str]) -> Optional[int]:
    """提取标题前缀编号（字母+三位数字），无编号返回 None"""
    if not text:
        return None
    match = _PREFIX_NUMBER_RE.match(str(text).strip())
    if not match:
        return None
    return int(match.group(2))


def normalize_weight_params(raw: Optional[dict]) -> Dict[str, Any]:
    """归一化权重参数：算法参数 + 默认值合并，非法类型回退默认。

    兼容旧版参数：若包含旧键（recent_order_bonus/sold_bonus）且不含任何新权重键，
    自动映射旧订单/售出力度为新权重，其余新权重置 0（中性）。
    """
    params = dict(DEFAULT_WEIGHT_PARAMS)
    if not raw:
        return params

    has_new_weights = any(k in raw for k in _WEIGHT_KEYS)

    if not has_new_weights:
        # 旧版参数 → 映射：订单信号切换为官方近7天成交，售出信号保留本地状态
        def _num(key: str) -> Optional[float]:
            v = raw.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        recent_order = _num("recent_order_bonus")
        if recent_order is not None:
            params["w_sale"] = int(recent_order)
        sold = _num("sold_bonus")
        if sold is not None:
            params["w_sold"] = int(sold)
        # 旧版没有的官方信号（曝光/浏览/咨询/转化率/想要）保持中性 0
        for key in ("w_exposure", "w_browse", "w_chat", "w_ucvr", "w_want"):
            params[key] = 0

    # 数值类参数
    for key in _WEIGHT_KEYS + ("first_use_bonus", "offline_recover_per_day",
                               "deleted_recover_per_day", "fail_penalty"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            params[key] = int(v)

    # 枚举类
    if raw.get("sample_mode") in SAMPLE_MODES:
        params["sample_mode"] = raw["sample_mode"]
    if raw.get("norm_method") in NORM_METHODS:
        params["norm_method"] = raw["norm_method"]

    # 布尔开关
    if isinstance(raw.get("exclude_sold"), bool):
        params["exclude_sold"] = raw["exclude_sold"]

    return params


async def compute_material_weights(
    user_id: int,
    materials: List[dict],
    params: Optional[dict] = None,
    session=None,
) -> List[Tuple[dict, int]]:
    """计算素材权重，返回 [(material, weight), ...] 按权重降序（执行器加权随机用）"""
    details = await compute_material_weight_details(
        user_id, materials, params=params, session=session
    )
    return [(d["material"], d["weight"]) for d in details]


async def compute_material_weight_details(
    user_id: int,
    materials: List[dict],
    params: Optional[dict] = None,
    session=None,
) -> List[Dict[str, Any]]:
    """计算素材权重并附带信号明细，返回 [{"material","weight","signals","parts","p_values"}, ...]
    按权重降序。

    signals: item_status/first_use/sold/offline_days/deleted_days/fail_count（系统信号）
             与 show_pv_7d/ipv_7d/chat_uv_7d/pay_ord_cnt_7d/ucvr_7d/want_total/no_data（官方信号），
    供算法效果预览展示。

    Args:
        user_id: 规则所属用户
        materials: 素材池 dict 列表（含 id/title）
        params: 权重参数（None 用系统默认）
        session: 复用调用方的 DB session（不传则内部新建）
    """
    from common.db.session import async_session_maker
    from common.models.item_stats_daily import ItemStatsDaily
    from common.models.publish_log import PublishLog
    from common.models.xy_catalog_item import XYCatalogItem
    from common.services.delist_scoring import _log_normalize, _value_percentiles
    from common.utils.time_utils import get_beijing_now

    p = normalize_weight_params(params)
    own_session = session is None
    if own_session:
        session = async_session_maker()

    try:
        now = get_beijing_now()
        now_naive = now.replace(tzinfo=None)

        numbers = {m.get("id"): extract_prefix_number(m.get("title")) for m in materials}

        # 1. 本地商品记录：编号 -> (状态, offline_at, deleted_at)（多记录按优先级聚合）
        from common.services.item_service import _normalize_item_status

        catalog_rows = (
            await session.execute(
                select(
                    XYCatalogItem.item_id,
                    XYCatalogItem.title,
                    XYCatalogItem.metadata_json,
                ).where(XYCatalogItem.owner_id == user_id)
            )
        ).all()
        state_map: Dict[int, Dict[str, Any]] = {}
        for item_id, title, meta in catalog_rows:
            num = extract_prefix_number(title)
            if num is None:
                continue
            m = meta or {}
            state = _normalize_item_status(m.get("item_status"))
            entry = state_map.setdefault(num, {"status": None, "offline_at": None, "deleted_at": None})
            # 状态聚合：在售优先于其他（用于排除/加权判断），避免依赖 DB 行序
            if entry["status"] is None or _STATUS_PRIORITY.get(state, 99) < _STATUS_PRIORITY.get(
                entry["status"], 99
            ):
                entry["status"] = state
            for key in ("offline_at", "deleted_at"):
                value = m.get(key)
                if value:
                    try:
                        from datetime import datetime as _dt
                        dt_value = _dt.fromisoformat(str(value))
                        if dt_value.tzinfo is not None:
                            dt_value = dt_value.replace(tzinfo=None)
                        if entry[key] is None or dt_value > entry[key]:
                            entry[key] = dt_value
                    except ValueError:
                        pass

        # 2. 官方快照按编号聚合：该编号商品的最新快照求和（同一商品只计一次）
        item_titles = {str(item_id): title for item_id, title, _ in catalog_rows}
        num_by_item: Dict[str, Optional[int]] = {
            str(item_id): extract_prefix_number(title) for item_id, title, _ in catalog_rows
        }
        stat_item_ids = [iid for iid in item_titles if num_by_item.get(iid) is not None]
        agg_map: Dict[int, Dict[str, Any]] = {}  # 编号 -> 聚合指标
        if stat_item_ids:
            max_sub = (
                select(ItemStatsDaily.item_id, func.max(ItemStatsDaily.stat_date).label("md"))
                .where(ItemStatsDaily.item_id.in_(stat_item_ids))
                .group_by(ItemStatsDaily.item_id)
                .subquery()
            )
            latest_rows = (
                await session.execute(
                    select(ItemStatsDaily).join(
                        max_sub,
                        and_(
                            ItemStatsDaily.item_id == max_sub.c.item_id,
                            ItemStatsDaily.stat_date == max_sub.c.md,
                        ),
                    )
                )
            ).scalars().all()
            for row in latest_rows:
                num = num_by_item.get(str(row.item_id))
                if num is None:
                    continue
                agg = agg_map.setdefault(num, {
                    "show_pv": 0, "ipv": 0, "chat_uv": 0, "pay_ord_cnt": 0,
                    "pay_amt": 0.0, "want": 0, "days_on_shelf": 0,
                })
                agg["show_pv"] += row.show_pv_7d or 0
                agg["ipv"] += row.ipv_7d or 0
                agg["chat_uv"] += row.chat_uv_7d or 0
                agg["pay_ord_cnt"] += row.pay_ord_cnt_7d or 0
                try:
                    agg["pay_amt"] += float(row.pay_amt_7d or 0)
                except (TypeError, ValueError):
                    pass
                agg["want"] += row.want_count or 0
                agg["days_on_shelf"] = max(agg["days_on_shelf"], row.days_on_shelf or 0)

        # 3. 近60天发布失败次数 + 从未发布判定（按素材ID）
        log_rows = (
            await session.execute(
                select(
                    PublishLog.material_id,
                    PublishLog.status,
                    PublishLog.created_at,
                ).where(PublishLog.user_id == user_id, PublishLog.material_id.isnot(None))
            )
        ).all()
        recent_fail_counts: Dict[int, int] = {}
        published_ids: set = set()
        for mid, status, created_at in log_rows:
            published_ids.add(mid)
            if status == "failed" and created_at is not None:
                ts = created_at.replace(tzinfo=None) if getattr(created_at, "tzinfo", None) else created_at
                if ts >= now_naive - timedelta(days=60):
                    recent_fail_counts[mid] = recent_fail_counts.get(mid, 0) + 1

        used_numbers: set = set()
        for iid, title, meta in catalog_rows:
            num = extract_prefix_number(title)
            if num is not None:
                used_numbers.add(num)

        # 5. 官方信号归一化（素材池内，仅对「有官方数据」的素材参与排序）
        metric_defs = [
            ("exposure", lambda a: float(a["show_pv"])),
            ("browse", lambda a: float(a["ipv"])),
            ("chat", lambda a: float(a["chat_uv"])),
            ("sale", lambda a: float(a["pay_ord_cnt"])),
            ("ucvr", lambda a: (a["pay_ord_cnt"] / a["ipv"]) if a["ipv"] > 0 else 0.0),
            ("want", lambda a: float(a["want"])),
        ]
        data_nums = list(agg_map.keys())
        data_list = [agg_map[num] for num in data_nums]
        norm_map: Dict[str, Dict[int, float]] = {name: {} for name, _ in metric_defs}
        for name, fn in metric_defs:
            values = [fn(a) for a in data_list]
            if p["norm_method"] == "log":
                norms = _log_normalize(values)
            else:
                # 按值取分位（相同值同分），再按原始顺序还原
                p_by_value = _value_percentiles(values)
                norms = [p_by_value[v] for v in values]
            for num, nv in zip(data_nums, norms):
                norm_map[name][num] = nv

        def _p_for(name: str, num: Optional[int], neutral: float = 0.5) -> float:
            if num is None or num not in data_nums:
                return neutral
            return norm_map[name].get(num, neutral)

        # 6. 逐素材计算权重（附信号明细与逐项分值，供预览展示）
        scored: List[Dict[str, Any]] = []
        for m in materials:
            num = numbers.get(m.get("id"))
            state_info = state_map.get(num) if num is not None else None
            offline_at = state_info.get("offline_at") if state_info else None
            deleted_at = state_info.get("deleted_at") if state_info else None
            offline_days = max((now_naive - offline_at).days, 0) if offline_at else None
            deleted_days = max((now_naive - deleted_at).days, 0) if deleted_at else None

            first_use = True
            first_use_add = 0
            sold = bool(state_info and state_info.get("status") == "sold")
            sold_add = 0
            offline_pen = 0
            deleted_pen = 0
            fail_pen = 0

            agg = agg_map.get(num) if num is not None else None
            no_data = agg is None

            if num is not None:
                if num in used_numbers or m.get("id") in published_ids:
                    first_use = False
                if first_use:
                    first_use_add = int(p["first_use_bonus"])
                if sold:
                    sold_add = int(p["w_sold"])
                if offline_days is not None:
                    offline_pen = -int(max(0, 100 - offline_days * float(p["offline_recover_per_day"])))
                if deleted_days is not None:
                    deleted_pen = -int(max(0, 100 - deleted_days * float(p["deleted_recover_per_day"])))
            else:
                # 无编号素材：无历史信号，按首次处理
                if m.get("id") in published_ids:
                    first_use = False
                if first_use:
                    first_use_add = int(p["first_use_bonus"])

            fails = recent_fail_counts.get(m.get("id"), 0)
            if fails:
                fail_pen = -int(p["fail_penalty"]) * fails

            pv = {name: _p_for(name, num) for name, _ in metric_defs}
            parts = {
                "base": 100,
                "first_use_bonus": first_use_add,
                "exposure": int(round(float(p["w_exposure"]) * pv["exposure"])),
                "browse": int(round(float(p["w_browse"]) * pv["browse"])),
                "chat": int(round(float(p["w_chat"]) * pv["chat"])),
                "sale": int(round(float(p["w_sale"]) * pv["sale"])),
                "ucvr": int(round(float(p["w_ucvr"]) * pv["ucvr"])),
                "want": int(round(float(p["w_want"]) * pv["want"])),
                "sold": sold_add,
                "offline_penalty": offline_pen,
                "deleted_penalty": deleted_pen,
                "fail_penalty": fail_pen,
            }

            raw_weight = sum(parts.values())
            weight = max(raw_weight, 1)
            scored.append({
                "material": m,
                "weight": int(weight),
                "signals": {
                    "item_status": state_info.get("status") if state_info else None,
                    "first_use": first_use,
                    "sold": sold,
                    "offline_days": offline_days,
                    "deleted_days": deleted_days,
                    "fail_count": fails,
                    "show_pv_7d": agg["show_pv"] if agg else None,
                    "ipv_7d": agg["ipv"] if agg else None,
                    "chat_uv_7d": agg["chat_uv"] if agg else None,
                    "pay_ord_cnt_7d": agg["pay_ord_cnt"] if agg else None,
                    "ucvr_7d": (agg["pay_ord_cnt"] / agg["ipv"]) if agg and agg["ipv"] > 0 else None,
                    "want_total": agg["want"] if agg else None,
                    "no_data": no_data,
                },
                "parts": parts,
                "p_values": {name: round(pv[name], 4) for name, _ in metric_defs},
                "clamped": raw_weight < 1,
            })

        scored.sort(key=lambda t: t["weight"], reverse=True)
        return scored
    finally:
        if own_session and session is not None:
            await session.close()


async def get_weight_algorithm_params(algorithm_id: Optional[int], session=None) -> Dict[str, Any]:
    """加载权重算法参数；算法不存在/停用/未选择时回退系统默认"""
    from common.db.session import async_session_maker
    from common.models.weight_algorithm import WeightAlgorithm

    if algorithm_id is None:
        return dict(DEFAULT_WEIGHT_PARAMS)

    own_session = session is None
    if own_session:
        session = async_session_maker()
    try:
        row = (
            await session.execute(
                select(WeightAlgorithm).where(
                    WeightAlgorithm.id == algorithm_id,
                    WeightAlgorithm.enabled == True,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return dict(DEFAULT_WEIGHT_PARAMS)
        return normalize_weight_params(row.params)
    finally:
        if own_session and session is not None:
            await session.close()
