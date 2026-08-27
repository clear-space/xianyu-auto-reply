"""
商品下架权重计算服务

功能：
1. 采集在售商品的下架信号（上架天数/无订单天数/近30天订单/擦亮状态）
2. 根据下架权重算法参数为每个商品计算下架权重（下限0分，0分不参与下架）
3. 供定时下架规则的选品使用（管理员定义的算法参数驱动）

权重公式：
    base_score 基础分
    + min(上架天数, age_cap_days) × age_points_per_day          （上架越久越该下）
    + min(无订单天数, no_order_cap_days) × no_order_points_per_day （无单越久越该下）
    - recent_order_penalty × [近30天有订单]                      （近期有单保护）
    - polished_penalty × [已擦亮]                                （近期活跃保护）
    下限 0 分（0 = 不参与下架）

无订单天数 = 最近订单距今；无任何订单记录时按上架天数计。
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

# 系统默认下架权重参数（规则未选择算法时使用）
DEFAULT_DELIST_PARAMS: Dict[str, Any] = {
    "base_score": 100,
    "age_points_per_day": 2,
    "age_cap_days": 100,
    "no_order_points_per_day": 8,
    "no_order_cap_days": 30,
    "recent_order_penalty": 120,
    "polished_penalty": 60,
    "min_score": 0,
    # 选取方式：top-按权重直选（高分必先下）；weighted-加权随机（权重=概率）
    "sample_mode": "top",
    "exclude_recent_order": False,
    "exclude_polished": False,
}

# 选取方式合法值
SAMPLE_MODES = ("weighted", "top")

# 权重参数白名单（加载算法参数时只取合法键，防脏数据）
_PARAM_KEYS = set(DEFAULT_DELIST_PARAMS.keys())

# 布尔开关类参数键
_BOOL_KEYS = ("exclude_recent_order", "exclude_polished")


def normalize_delist_params(raw: Optional[dict]) -> Dict[str, Any]:
    """归一化下架权重参数：算法参数 + 默认值合并，非法类型回退默认"""
    params = dict(DEFAULT_DELIST_PARAMS)
    if raw:
        for key in _PARAM_KEYS:
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                params[key] = value
            elif isinstance(value, bool) and key in _BOOL_KEYS:
                params[key] = bool(value)
            elif key == "sample_mode" and value in SAMPLE_MODES:
                params[key] = value
    return params


async def compute_delist_scores(
    user_id: int,
    account_id: str,
    items: List[Any],
    params: Optional[dict] = None,
    session=None,
) -> List[Dict[str, Any]]:
    """计算账号内在售商品的下架权重，返回按权重降序的明细列表。

    返回 [{item, weight, signals, parts, clamped}, ...]
    signals: age_days/no_order_days/recent_order/polished，
    parts: 逐项分值构成，供算法效果预览与执行明细展示。

    Args:
        user_id: 规则所属用户
        account_id: 商品所属闲鱼账号ID（订单信号按账号隔离）
        items: 该账号在售商品（XYCatalogItem 行列表，调用方已过滤状态）
        params: 权重参数（None 用系统默认）
        session: 复用调用方的 DB session（不传则内部新建）
    """
    from common.db.session import async_session_maker
    from common.models.xy_order import XYOrder
    from common.services.item_service import get_item_publish_time
    from common.utils.time_utils import get_beijing_now

    p = normalize_delist_params(params)
    own_session = session is None
    if own_session:
        session = async_session_maker()

    try:
        now = get_beijing_now()
        recent_cutoff = now - timedelta(days=30)

        # 该账号全部订单：item_id -> 最近订单时间（无记录 = 从未有单）
        order_rows = (
            await session.execute(
                select(XYOrder.item_id, XYOrder.created_at).where(
                    XYOrder.owner_id == user_id,
                    XYOrder.account_id == account_id,
                    XYOrder.item_id.isnot(None),
                )
            )
        ).all()
        latest_order: Dict[str, Any] = {}
        for item_id, created_at in order_rows:
            if created_at is None:
                continue
            ts = (
                created_at.replace(tzinfo=timezone.utc)
                if getattr(created_at, "tzinfo", None) is None
                else created_at
            )
            if item_id not in latest_order or ts > latest_order[item_id]:
                latest_order[item_id] = ts

        age_cap = int(p["age_cap_days"])
        no_order_cap = int(p["no_order_cap_days"])

        scored: List[Dict[str, Any]] = []
        for row in items:
            publish_at = get_item_publish_time(row.metadata_json, row.created_at)
            age_days = max((now - publish_at).days, 0) if publish_at else 0

            last_order = latest_order.get(row.item_id)
            if last_order is not None:
                no_order_days = max((now - last_order).days, 0)
                recent_order = last_order >= recent_cutoff
            else:
                # 无订单记录：无订单天数按上架天数计
                no_order_days = age_days
                recent_order = False

            polished = bool(row.is_polished)

            age_add = int(min(age_days, age_cap) * float(p["age_points_per_day"]))
            no_order_add = int(
                min(no_order_days, no_order_cap) * float(p["no_order_points_per_day"])
            )
            order_pen = -int(p["recent_order_penalty"]) if recent_order else 0
            polished_pen = -int(p["polished_penalty"]) if polished else 0

            raw_weight = int(p["base_score"]) + age_add + no_order_add + order_pen + polished_pen
            weight = max(raw_weight, 0)
            scored.append({
                "item": row,
                "weight": int(weight),
                "signals": {
                    "age_days": age_days,
                    "no_order_days": no_order_days,
                    "recent_order": recent_order,
                    "polished": polished,
                },
                "parts": {
                    "base": int(p["base_score"]),
                    "age_points": age_add,
                    "no_order_points": no_order_add,
                    "recent_order_penalty": order_pen,
                    "polished_penalty": polished_pen,
                },
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
