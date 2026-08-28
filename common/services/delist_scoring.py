"""
商品下架权重计算服务（基于闲鱼官方运营数据）

功能：
1. 采集在售商品的官方运营信号（真实上架天数/曝光/浏览/咨询/成交/转化率/想要增速，
   数据来源 xy_item_stats_daily 每日快照表，由商品指标快照任务凌晨采集）
2. 各信号在账号内先做归一化（percentile/log），再乘以权重计入总分
3. 供定时下架规则选品与算法效果预览使用（管理员定义的算法参数驱动）

权重公式（无阈值，纯归一化加权）：
    weight = max(0,
      base_score
      + w_age      × p(真实上架天数)                # 老化：越久越该下
      + w_no_sale  × p(连续无成交天数)               # 连续无成交（快照逐日推算）
      + w_exposure × (1 − p(近7天曝光))             # 曝光越低分越高
      + w_browse   × (1 − p(近7天浏览))
      + w_chat     × (1 − p(近7天咨询))
      + w_sale     × (1 − p(近7天成交))
      + w_ucvr     × (1 − p(近7天转化率))
      + w_want     × (1 − p(想要7天增速))            # 掉想要 → 增速排低端 → 加分
      − w_polished × [已擦亮]                        # 布尔保护（系统动作）
    )

归一化：按账号内全部在售商品（含无快照按 0 计）计算各指标百分位，
无快照商品（当日新发布）默认排除（no_data_behavior=exclude，权重 0 不参与下架）。

兼容：旧版参数（age_points_per_day 等）自动映射为新权重（仅老化/擦亮生效，
其余新因子置 0 中性），老化天数切换为真实上架天数。
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select

# 系统默认下架权重参数（规则未选择算法时使用）
DEFAULT_DELIST_PARAMS: Dict[str, Any] = {
    "base_score": 100,
    "w_age": 80,
    "w_no_sale": 120,
    "w_exposure": 100,
    "w_browse": 40,
    "w_chat": 30,
    "w_sale": 100,
    "w_ucvr": 30,
    "w_want": 40,
    "w_polished": 30,
    "min_score": 0,
    # 选取方式：top-按权重直选（高分必先下）；weighted-加权随机（权重=概率）
    "sample_mode": "top",
    # 归一化方式：percentile-账号内百分位；log-对数归一化
    "norm_method": "percentile",
    # 无快照商品处理：exclude-权重0不参与下架；base-仅按基础分参与
    "no_data_behavior": "exclude",
    # 硬排开关（兼容旧算法）：开启后满足条件的商品权重直接置 0
    "exclude_recent_order": False,
    "exclude_polished": False,
}

SAMPLE_MODES = ("weighted", "top")
NORM_METHODS = ("percentile", "log")
NO_DATA_BEHAVIORS = ("exclude", "base")

# 新版权重参数键
_WEIGHT_KEYS = ("w_age", "w_no_sale", "w_exposure", "w_browse", "w_chat",
                "w_sale", "w_ucvr", "w_want", "w_polished")
# 通用参数键
_COMMON_KEYS = ("base_score", "min_score", "sample_mode", "norm_method",
                "no_data_behavior", "exclude_recent_order", "exclude_polished")
_BOOL_KEYS = ("exclude_recent_order", "exclude_polished")


def normalize_delist_params(raw: Optional[dict]) -> Dict[str, Any]:
    """归一化下架权重参数：算法参数 + 默认值合并，非法类型回退默认。

    兼容旧版参数：若包含旧键（age_points_per_day 等）且不含任何新权重键，
    自动映射旧老化/擦亮力度为新权重，其余新因子置 0（中性）。
    """
    params = dict(DEFAULT_DELIST_PARAMS)
    if not raw:
        return params

    has_new_weights = any(k in raw for k in _WEIGHT_KEYS)

    if not has_new_weights:
        # 旧版参数 → 映射（保留旧算法四类信号的力度，数据源切换为官方真实数据）：
        # 老化/无单取旧参数的最大累计分，近期订单保护映射为成交反向权重，擦亮直接映射
        def _num(key: str) -> Optional[float]:
            v = raw.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        age_pt, age_cap = _num("age_points_per_day"), _num("age_cap_days")
        if age_pt is not None and age_cap is not None:
            params["w_age"] = min(int(age_pt * age_cap), 400)
        no_pt, no_cap = _num("no_order_points_per_day"), _num("no_order_cap_days")
        if no_pt is not None and no_cap is not None:
            params["w_no_sale"] = min(int(no_pt * no_cap), 400)
        recent = _num("recent_order_penalty")
        if recent is not None:
            params["w_sale"] = int(recent)
        polish = _num("polished_penalty")
        if polish is not None:
            params["w_polished"] = int(polish)
        # 旧版没有的信号（曝光/浏览/咨询/转化率/想要）保持中性 0
        for key in ("w_exposure", "w_browse", "w_chat", "w_ucvr", "w_want"):
            params[key] = 0

    # 数值类参数（新权重 + 通用数值）
    for key in _WEIGHT_KEYS + ("base_score", "min_score"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            params[key] = int(v)

    # 枚举类
    if raw.get("sample_mode") in SAMPLE_MODES:
        params["sample_mode"] = raw["sample_mode"]
    if raw.get("norm_method") in NORM_METHODS:
        params["norm_method"] = raw["norm_method"]
    if raw.get("no_data_behavior") in NO_DATA_BEHAVIORS:
        params["no_data_behavior"] = raw["no_data_behavior"]

    # 布尔开关
    for key in _BOOL_KEYS:
        if isinstance(raw.get(key), bool):
            params[key] = raw[key]

    return params


def _midrank_percentile(sorted_values: List[float], n: int) -> List[float]:
    """对已升序数组计算各元素的百分位（中位秩 p ∈ (0,1]）"""
    if n == 0:
        return []
    result: List[float] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        mid = (i + j) / 2.0
        p = mid / n
        for _ in range(i, j + 1):
            result.append(p)
        i = j + 1
    return result


def _log_normalize(values: List[float]) -> List[float]:
    """对数归一化：p = log(1+x)/log(1+max)（负值先归 0）"""
    mx = max(values) if values else 0.0
    if mx <= 0:
        return [0.5 for _ in values]  # 全员 0：中性
    denom = math.log(1.0 + mx)
    return [math.log(1.0 + max(v, 0.0)) / denom for v in values]


def _to_float(value: Any) -> float:
    """安全转 float；None/非法/- 视为 0（最差端）"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("%", "")
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


async def compute_delist_scores(
    user_id: int,
    account_id: str,
    items: List[Any],
    params: Optional[dict] = None,
    session=None,
) -> List[Dict[str, Any]]:
    """计算账号内在售商品的下架权重，返回按权重降序的明细列表。

    返回 [{item, weight, signals, parts, p_values, clamped, no_data}, ...]
    - signals: 原始信号（真实上架天数/曝光/浏览/咨询/成交/转化率/想要增速/连续无成交天数）
    - parts: 逐项分值构成，供算法效果预览与执行明细展示
    - p_values: 各信号归一化值（百分位），供预览透明化

    Args:
        user_id: 规则所属用户
        account_id: 商品所属闲鱼账号ID
        items: 该账号在售商品（XYCatalogItem 行列表，调用方已过滤状态）
        params: 权重参数（None 用系统默认）
        session: 复用调用方的 DB session（不传则内部新建）
    """
    from common.db.session import async_session_maker
    from common.models.item_stats_daily import ItemStatsDaily
    from common.utils.time_utils import get_beijing_now

    p = normalize_delist_params(params)
    own_session = session is None
    if own_session:
        session = async_session_maker()

    try:
        item_ids = [row.item_id for row in items]
        latest_map: Dict[str, Any] = {}
        history_map: Dict[str, List[tuple]] = {}  # item_id -> [(stat_date, pay_ord_cnt_1d, want_count)]

        if item_ids:
            # 最新快照（每商品 stat_date 最大的一行）
            max_sub = (
                select(ItemStatsDaily.item_id, func.max(ItemStatsDaily.stat_date).label("md"))
                .where(
                    ItemStatsDaily.account_id == account_id,
                    ItemStatsDaily.item_id.in_(item_ids),
                )
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
            latest_map = {str(r.item_id): r for r in latest_rows}

            # 历史序列（近 30 天，用于连续无成交天数与想要增速）
            hist_rows = (
                await session.execute(
                    select(
                        ItemStatsDaily.item_id,
                        ItemStatsDaily.stat_date,
                        ItemStatsDaily.pay_ord_cnt_1d,
                        ItemStatsDaily.want_count,
                    )
                    .where(
                        ItemStatsDaily.account_id == account_id,
                        ItemStatsDaily.item_id.in_(item_ids),
                    )
                    .order_by(ItemStatsDaily.item_id, ItemStatsDaily.stat_date.desc())
                )
            ).all()
            for item_id, stat_date, cnt, want in hist_rows:
                history_map.setdefault(str(item_id), []).append(
                    (stat_date, cnt if cnt is not None else 0, want)
                )

        # ---------- 组装原始信号 ----------
        def _hist_sorted(key: str) -> List[tuple]:
            return sorted(history_map.get(key, []), key=lambda t: t[0], reverse=True)

        signals_rows: List[Dict[str, Any]] = []
        for row in items:
            key = str(row.item_id)
            latest = latest_map.get(key)
            if latest is None:
                signals_rows.append({
                    "item": row, "no_data": True,
                    "age_days": 0, "no_sale_days": 0,
                    "show_pv": None, "ipv": None, "chat_uv": None,
                    "pay_ord_cnt": None, "ucvr": None,
                    "want_growth": None, "want_now": None, "polished": bool(row.is_polished),
                })
                continue

            age_days = latest.days_on_shelf if latest.days_on_shelf is not None else 0

            # 连续无成交天数：从最新快照往回数 pay_ord_cnt_1d==0；遇成交或日期断档停止
            hist = _hist_sorted(key)
            no_sale_days = 0
            from datetime import datetime, timedelta

            if hist:
                cur = datetime.strptime(hist[0][0], "%Y%m%d")
                for stat_date, cnt, _ in hist:
                    d = datetime.strptime(stat_date, "%Y%m%d")
                    if (cur - d).days > 1:
                        break
                    if cnt > 0:
                        break
                    no_sale_days += 1
                    cur = d

            # 想要 7 天增速：want_now − 7天前（历史中距 latest-7d 最近的一行）
            want_now = latest.want_count
            want_growth = 0
            if hist:
                latest_dt = datetime.strptime(hist[0][0], "%Y%m%d")
                target = latest_dt - timedelta(days=7)
                ref_row = None
                for stat_date, _, want in hist:
                    d = datetime.strptime(stat_date, "%Y%m%d")
                    if d <= target:
                        ref_row = (d, want)
                        break
                if ref_row is not None and want_now is not None and ref_row[1] is not None:
                    want_growth = want_now - ref_row[1]

            signals_rows.append({
                "item": row, "no_data": False,
                "age_days": age_days, "no_sale_days": no_sale_days,
                "show_pv": latest.show_pv_7d, "ipv": latest.ipv_7d,
                "chat_uv": latest.chat_uv_7d, "pay_ord_cnt": latest.pay_ord_cnt_7d,
                "ucvr": latest.ipv_pay_ucvr_7d,
                "want_growth": want_growth, "want_now": want_now,
                "polished": bool(row.is_polished),
            })

        # ---------- 账号内归一化 ----------
        metric_defs = [
            ("age", lambda s: float(s["age_days"])),
            ("no_sale", lambda s: float(s["no_sale_days"])),
            ("exposure", lambda s: _to_float(s["show_pv"])),
            ("browse", lambda s: _to_float(s["ipv"])),
            ("chat", lambda s: _to_float(s["chat_uv"])),
            ("sale", lambda s: _to_float(s["pay_ord_cnt"])),
            ("ucvr", lambda s: _to_float(s["ucvr"])),
            ("want", lambda s: float(s["want_growth"] or 0)),
        ]
        # 归一化在全体商品上计算（无快照按 0 参与，保持 n 一致）
        raw_values = {name: [fn(s) for s in signals_rows] for name, fn in metric_defs}
        norm_map: Dict[str, List[float]] = {}
        for name, _ in metric_defs:
            values = raw_values[name]
            if p["norm_method"] == "log":
                norm_map[name] = _log_normalize(values)
            else:
                sorted_vals = sorted(values)
                norm_map[name] = _midrank_percentile(sorted_vals, len(sorted_vals))

        # ---------- 计分 ----------
        weights = {k: float(p[k]) for k in _WEIGHT_KEYS}
        base = int(p["base_score"])
        exclude_recent = bool(p["exclude_recent_order"])
        exclude_polished = bool(p["exclude_polished"])

        scored: List[Dict[str, Any]] = []
        for idx, s in enumerate(signals_rows):
            row = s["item"]
            if s["no_data"] and p["no_data_behavior"] == "exclude":
                # 无快照商品（当日新发布）：权重 0 不参与下架
                scored.append({
                    "item": row, "weight": 0,
                    "signals": {
                        "age_days": 0, "no_sale_days": 0, "polished": s["polished"],
                        "show_pv_7d": None, "ipv_7d": None, "chat_uv_7d": None,
                        "pay_ord_cnt_7d": None, "ucvr_7d": None,
                        "want_growth_7d": None, "want_now": None,
                        "no_data": True, "excluded": False,
                    },
                    "parts": {"base": 0, "age": 0, "no_sale": 0, "exposure": 0,
                              "browse": 0, "chat": 0, "sale": 0, "ucvr": 0,
                              "want": 0, "polished": 0},
                    "p_values": {},
                    "clamped": False,
                })
                continue

            nv = {name: norm_map[name][idx] for name, _ in metric_defs}
            # 无快照且 no_data_behavior=base：仅基础分参与，归一化项中性（0.5）
            neutral = 0.5 if s["no_data"] else None
            parts = {
                "base": base,
                "age": int(round(weights["w_age"] * (neutral if neutral is not None else nv["age"]))),
                "no_sale": int(round(weights["w_no_sale"] * (neutral if neutral is not None else nv["no_sale"]))),
                "exposure": int(round(weights["w_exposure"] * (neutral if neutral is not None else (1 - nv["exposure"])))),
                "browse": int(round(weights["w_browse"] * (neutral if neutral is not None else (1 - nv["browse"])))),
                "chat": int(round(weights["w_chat"] * (neutral if neutral is not None else (1 - nv["chat"])))),
                "sale": int(round(weights["w_sale"] * (neutral if neutral is not None else (1 - nv["sale"])))),
                "ucvr": int(round(weights["w_ucvr"] * (neutral if neutral is not None else (1 - nv["ucvr"])))),
                "want": int(round(weights["w_want"] * (neutral if neutral is not None else (1 - nv["want"])))),
                "polished": -int(weights["w_polished"]) if s["polished"] else 0,
            }

            raw_weight = sum(parts.values())
            # 硬排开关（兼容旧算法）：命中后权重直接置 0（执行器按权重>0 过滤，不参与下架）
            excluded = False
            if exclude_recent and s["pay_ord_cnt"] is not None and s["pay_ord_cnt"] > 0:
                excluded = True
            if exclude_polished and s["polished"]:
                excluded = True
            # 下限 0 分（min_score 过滤由执行器/调用方负责，保持旧语义）
            weight = 0 if excluded else max(int(raw_weight), 0)

            scored.append({
                "item": row,
                "weight": int(weight),
                "signals": {
                    "age_days": s["age_days"],
                    "no_sale_days": s["no_sale_days"],
                    "polished": s["polished"],
                    "show_pv_7d": s["show_pv"],
                    "ipv_7d": s["ipv"],
                    "chat_uv_7d": s["chat_uv"],
                    "pay_ord_cnt_7d": s["pay_ord_cnt"],
                    "ucvr_7d": s["ucvr"],
                    "want_growth_7d": s["want_growth"],
                    "want_now": s["want_now"],
                    "no_data": False,
                    "excluded": excluded,
                },
                "parts": parts,
                "p_values": {name: round(nv[name], 4) for name, _ in metric_defs},
                "clamped": raw_weight < 0,
            })

        scored.sort(key=lambda t: t["weight"], reverse=True)
        return scored
    finally:
        if own_session and session is not None:
            await session.close()


async def get_delist_algorithm_params(algorithm_id: Optional[int], session=None) -> Dict[str, Any]:
    """加载下架权重算法参数；算法不存在/停用/未选择时回退系统默认"""
    from common.db.session import async_session_maker
    from common.models.weight_algorithm import WeightAlgorithm

    if algorithm_id is None:
        return dict(DEFAULT_DELIST_PARAMS)

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
            return dict(DEFAULT_DELIST_PARAMS)
        return normalize_delist_params(row.params)
    finally:
        if own_session and session is not None:
            await session.close()
