"""
商品素材权重计算服务

功能：
1. 按编号采集素材的历史信号（首次使用/订单/售出/下架/删除/发布失败）
2. 根据权重算法参数为每个素材计算权重（保底1分，永不彻底排除）
3. 供定时发布随机模式的加权选料使用（管理员定义的算法参数驱动）

权重公式：
    100 基础分
    + first_use_bonus          从未使用过（编号未出现在本地商品/发布日志/订单任何一处）
    + recent_order_bonus       近30天有订单
    + sold_bonus               已售出状态
    - max(0, 100 - 下架天数 × offline_recover_per_day)
    - max(0, 100 - 删除天数 × deleted_recover_per_day)
    - fail_penalty × 近60天发布失败次数
    保底 1 分
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

# 素材标题前缀编号：一个字母 + 三位数字（与一键关联卡券共用同一规则）
_PREFIX_NUMBER_RE = re.compile(r"^([A-Za-z])(\d{3})(?!\d)")

# 系统默认权重参数（规则未选择算法时使用）
DEFAULT_WEIGHT_PARAMS: Dict[str, Any] = {
    "first_use_bonus": 50,
    "recent_order_bonus": 30,
    "sold_bonus": 25,
    "offline_recover_per_day": 2,
    "deleted_recover_per_day": 1,
    "fail_penalty": 10,
    "exclude_sold": False,
    # 选料方式：weighted-加权随机（权重=概率）；top-按权重直选（高分必先选）
    "sample_mode": "weighted",
}

# 选料方式合法值
SAMPLE_MODES = ("weighted", "top")

# 权重参数白名单（加载算法参数时只取合法键，防脏数据）
_PARAM_KEYS = set(DEFAULT_WEIGHT_PARAMS.keys())

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
    """归一化权重参数：算法参数 + 默认值合并，非法类型回退默认"""
    params = dict(DEFAULT_WEIGHT_PARAMS)
    if raw:
        for key in _PARAM_KEYS:
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                params[key] = value
            elif isinstance(value, bool) and key == "exclude_sold":
                params[key] = bool(value)
            elif key == "sample_mode" and value in SAMPLE_MODES:
                params[key] = value
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
    """计算素材权重并附带信号明细，返回 [{"material","weight","signals"}, ...] 按权重降序。

    signals: item_status/first_use/recent_order/sold/offline_days/deleted_days/fail_count，
    供算法效果预览展示。

    Args:
        user_id: 规则所属用户
        materials: 素材池 dict 列表（含 id/title）
        params: 权重参数（None 用系统默认）
        session: 复用调用方的 DB session（不传则内部新建）
    """
    from common.db.session import async_session_maker
    from common.models.publish_log import PublishLog
    from common.models.xy_catalog_item import XYCatalogItem
    from common.models.xy_order import XYOrder
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

        # 2. 近30天有订单的编号集合（订单 item_id 需通过本地商品标题还原编号）
        order_rows = (
            await session.execute(
                select(XYOrder.item_id).where(
                    XYOrder.owner_id == user_id,
                    XYOrder.created_at >= now_naive - timedelta(days=30),
                    XYOrder.item_id.isnot(None),
                )
            )
        ).all()
        recent_order_item_ids = {r[0] for r in order_rows}
        recent_order_nos: set = set()
        if recent_order_item_ids:
            title_map = {}
            for item_id, title, meta in catalog_rows:
                title_map[str(item_id)] = title
            for iid in recent_order_item_ids:
                num = extract_prefix_number(title_map.get(str(iid)))
                if num is not None:
                    recent_order_nos.add(num)

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

        # 4. 逐素材计算权重（附信号明细与逐项分值，供预览展示）
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
            order_add = 0
            sold_add = 0
            offline_pen = 0
            deleted_pen = 0
            fail_pen = 0

            if num is not None:
                if num in used_numbers or m.get("id") in published_ids:
                    first_use = False
                if first_use:
                    first_use_add = int(p["first_use_bonus"])
                else:
                    if num in recent_order_nos:
                        order_add = int(p["recent_order_bonus"])
                    if state_info and state_info.get("status") == "sold":
                        sold_add = int(p["sold_bonus"])
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

            raw_weight = 100 + first_use_add + order_add + sold_add + offline_pen + deleted_pen + fail_pen
            weight = max(raw_weight, 1)
            scored.append({
                "material": m,
                "weight": int(weight),
                "signals": {
                    "item_status": state_info.get("status") if state_info else None,
                    "first_use": first_use,
                    "recent_order": bool(num in recent_order_nos) if num is not None else False,
                    "sold": bool(state_info and state_info.get("status") == "sold"),
                    "offline_days": offline_days,
                    "deleted_days": deleted_days,
                    "fail_count": fails,
                },
                "parts": {
                    "base": 100,
                    "first_use_bonus": first_use_add,
                    "recent_order_bonus": order_add,
                    "sold_bonus": sold_add,
                    "offline_penalty": offline_pen,
                    "deleted_penalty": deleted_pen,
                    "fail_penalty": fail_pen,
                },
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
