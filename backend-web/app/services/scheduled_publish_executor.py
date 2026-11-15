"""
定时发布共享执行器

功能（随机选料 / 去重 / 自动补发 / 账号熔断 逻辑只维护一份，backend 手动触发与 scheduler 定时触发共用）：
1. 指定发布（specified）：发布全部所选素材
2. 随机发布（random）：每账号独立目标——每账号发布成功 random_count 条即达标
3. 去重（deduplicate，随机模式可用）：
   - 发布前刷新规则内账号的在售商品列表
   - 按素材标题前缀编号（A+数字）与该账号在售商品编号比对，已存在的过滤（按账号各自过滤）
   - 素材无编号则不过滤
4. 账号独立执行：选料/权重/去重/批次已发编号均按账号各自维护；
   某账号未达标时只补发该账号，不影响已达标账号
5. 账号熔断：账号连续 ACCOUNT_BREAKER_STREAK 轮无任何素材成功，或命中已知致命错误码
   （如闲鱼在售数量上限类错误），则本批次剩余轮次跳过该账号——防止单账号失败拖垮整条规则
6. 素材池耗尽仍未达标的账号记入 account_result（pool_exhausted）
7. 失败明细聚合为 fail_summary（错误码 + 原始原因 + 次数），写入 detail_json 供定时历史展示
"""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.publish_schedule_log import PublishScheduleLog
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.services.material_scoring import (
    DEFAULT_WEIGHT_PARAMS,
    compute_material_weights,
    get_weight_algorithm_params,
)
from common.services.item_service import _normalize_item_status
from common.utils.text_utils import extract_prefix_number as _extract_prefix_number
from common.utils.time_utils import get_beijing_now

# 账号熔断：连续无成功轮数阈值（每轮素材不同，连续多轮全败视为账号级问题）
ACCOUNT_BREAKER_STREAK = 3

# 已知的"账号级致命错误"标记：命中即立即熔断（无需等连续轮数）。
# 只匹配闲鱼业务错误码类别，不依赖任何具体数量（不同账号类型限额不同，由闲鱼 API 原文说明）。
_FATAL_ACCOUNT_ERROR_MARKERS = ("FAIL_BIZ_QUANTITY_ITEM_CAT_TOO_LARGE_NEW",)

# 失败原因里的错误码提取（闲鱼 mtop 业务错误码形如 FAIL_XXX_NEW）
_FAIL_CODE_RE = re.compile(r"FAIL_[A-Z0-9_]+")

# 已知失败原因的通用检查提示（不做具体数量解读，仅提醒人工检查）
_FAIL_HINT_MARKERS = (
    ("FAIL_BIZ_QUANTITY_ITEM_CAT_TOO_LARGE_NEW", "请检查该账号可发布商品数量上限"),
    ("图片上传失败", "请检查网络或稍后重试"),
    ("接口未返回有效图片地址", "请检查网络或稍后重试"),
)


def extract_item_number(title: Optional[str]) -> Optional[int]:
    """从素材/商品标题提取前缀编号（字母+三位数字，如 A014），无编号返回 None。

    编号解析统一在 common.utils.text_utils.extract_prefix_number（与一键关联卡券共用）。
    """
    key = _extract_prefix_number(title)
    return key[1] if key else None


class ScheduledPublishExecutor:
    """定时发布执行器：手动触发路由与内部端点（scheduler 调用）共用同一份实现"""

    @staticmethod
    async def run(
        user_id: int,
        schedule_data: Dict[str, Any],
        batch_id: str,
        schedule_log_id: int,
    ) -> None:
        """
        执行一次定时发布（随机选料、去重、自动补发、账号熔断），完成后回写执行记录。

        Args:
            user_id: 规则所属用户ID
            schedule_data: 规则快照 dict（id/account_ids/material_ids/material_scope/publish_mode/random_count/deduplicate_enabled/weight_algorithm_id）
            batch_id: 本次执行的批次ID（执行记录与发布日志共用）
            schedule_log_id: 执行记录ID
        """
        from app.services.publish_execution_service import PublishExecutorService

        schedule_id = schedule_data.get("id")
        publish_mode = schedule_data.get("publish_mode") or "specified"
        random_count = int(schedule_data.get("random_count") or 0)
        account_ids = list(schedule_data.get("account_ids") or [])
        ordered_account_ids = list(dict.fromkeys(account_ids))
        deduplicate_enabled = bool(
            schedule_data.get("deduplicate_enabled") and publish_mode == "random"
        )

        # 权重算法参数（随机模式加权选料；算法停用/缺失自动回退系统默认）
        weight_algorithm_id = schedule_data.get("weight_algorithm_id")
        weight_algorithm_name = None
        weight_params = DEFAULT_WEIGHT_PARAMS
        if publish_mode == "random":
            try:
                weight_params = await get_weight_algorithm_params(weight_algorithm_id)
                weight_algorithm_name = await ScheduledPublishExecutor._get_algorithm_name(
                    weight_algorithm_id
                )
            except Exception as exc:
                logger.warning(
                    f"[热度加权] 规则 #{schedule_id} 加载权重算法失败（回退热度均衡）: {exc}"
                )

        # ========== 第 1 阶段：加载素材池 + 去重准备（独立 session） ==========
        # 硬排已售出（算法开关）不依赖去重开关：关去重时也读本地状态（可能滞后于最近一次同步）
        need_status_numbers = publish_mode == "random" and bool(
            weight_params.get("exclude_sold")
        )
        try:
            pool, on_sale_by_account, sold_by_account = await ScheduledPublishExecutor._prepare(
                user_id, schedule_data, deduplicate_enabled, need_status_numbers
            )
        except Exception as e:
            logger.error(f"[定时发布] 规则 #{schedule_id} 准备阶段失败: {e}")
            await ScheduledPublishExecutor._finalize_log(
                schedule_log_id,
                status="failed",
                error_message=f"准备阶段失败: {str(e)[:800]}",
            )
            return

        if not pool:
            logger.warning(f"[定时发布] 规则 #{schedule_id} 素材池为空（素材失效或全部被去重过滤）")
            await ScheduledPublishExecutor._finalize_log(
                schedule_log_id,
                status="failed",
                error_message="素材池为空（素材失效或全部被去重过滤）",
                detail_json={"publish_mode": publish_mode, "rounds": [], "filtered": []},
            )
            return

        # 随机模式：逐账号计算素材权重（信号随账号范围过滤——每账号一份权重表）
        weights_by_account: Dict[str, Dict[int, int]] = {}
        if publish_mode == "random":
            for aid in ordered_account_ids:
                try:
                    scored = await compute_material_weights(
                        user_id, pool, params=weight_params, account_ids=[aid]
                    )
                    weights_by_account[aid] = {m.get("id"): w for m, w in scored}
                except Exception as exc:
                    logger.warning(
                        f"[热度加权] 规则 #{schedule_id} 账号 {aid} 计算素材权重失败"
                        f"（退化为均匀随机）: {exc}"
                    )

        # ========== 第 2 阶段：账号独立发布循环（每账号每轮独立 session） ==========
        tried_by_account: Dict[str, Set[int]] = {}
        batch_numbers_by_account: Dict[str, Set[int]] = {}  # 本批次该账号已发布的编号
        filtered_ids: Set[int] = set()   # 已记录的去重过滤素材（避免多轮重复记录）
        account_ok: Dict[str, int] = {}      # 账号已发布成功的素材数（与 random_count 达标比对）
        account_fail: Dict[str, int] = {}    # 账号失败尝试数（素材×账号维度）
        fail_streak: Dict[str, int] = {}     # 账号连续无成功轮数（熔断判定）
        dead_accounts: Dict[str, Dict[str, Any]] = {}   # 已熔断账号
        pool_exhausted_accounts: Set[str] = set()
        total_attempts = 0
        detail_rounds: List[Dict[str, Any]] = []
        filtered_materials: List[Dict[str, Any]] = []
        round_no = 0

        while True:
            round_no += 1
            active = [
                aid for aid in ordered_account_ids
                if aid not in dead_accounts
                and (publish_mode != "random" or account_ok.get(aid, 0) < random_count)
            ]
            if not active:
                break

            round_detail: Dict[str, Any] = {"round": round_no, "accounts": []}
            round_progress = False

            for aid in active:
                # ---- 该账号视角的素材池（tried/去重/已售出均按账号独立）----
                remaining = [m for m in pool if m["id"] not in tried_by_account.get(aid, set())]

                if deduplicate_enabled:
                    blocked = (
                        on_sale_by_account.get(aid, set())
                        | batch_numbers_by_account.get(aid, set())
                    )
                    kept: List[dict] = []
                    for m in remaining:
                        if m.get("_item_no") is not None and m["_item_no"] in blocked:
                            if m["id"] not in filtered_ids:
                                filtered_ids.add(m["id"])
                                filtered_materials.append(
                                    {
                                        "material_id": m["id"],
                                        "item_no": f"A{m['_item_no']}",
                                        "round": round_no,
                                    }
                                )
                        else:
                            kept.append(m)
                    remaining = kept

                if publish_mode == "random" and weight_params.get("exclude_sold"):
                    sold_set = sold_by_account.get(aid, set())
                    remaining = [m for m in remaining if m.get("_item_no") not in sold_set]

                # ---- 选料 ----
                if publish_mode == "specified":
                    # 指定发布：第一轮发全部，无补发
                    pick = remaining if round_no == 1 else []
                else:
                    deficit = max(random_count - account_ok.get(aid, 0), 0)
                    if remaining and deficit > 0:
                        pick = ScheduledPublishExecutor._weighted_sample(
                            remaining,
                            weights_by_account.get(aid, {}),
                            min(deficit, len(remaining)),
                            sample_mode=weight_params.get("sample_mode"),
                        )
                    else:
                        pick = []

                if not pick:
                    if publish_mode != "specified" and aid not in pool_exhausted_accounts:
                        pool_exhausted_accounts.add(aid)
                        round_detail["accounts"].append(
                            {
                                "account_id": aid,
                                "status": "pool_exhausted",
                                "materials": [],
                                "note": "素材池耗尽或全部被去重过滤",
                            }
                        )
                    continue

                round_progress = True
                for m in pick:
                    tried_by_account.setdefault(aid, set()).add(m["id"])

                # ---- 发布：仅该账号（账号独立执行的唯一发布调用）----
                account_section: Dict[str, Any] = {"account_id": aid, "materials": []}
                try:
                    async with async_session_maker() as session:
                        svc = PublishExecutorService(session)
                        # 传给发布层前去掉内部辅助字段，避免影响发布 payload
                        publish_materials = [
                            {k: v for k, v in m.items() if k != "_item_no"} for m in pick
                        ]
                        result = await svc.batch_publish(
                            user_id=user_id,
                            account_ids=[aid],
                            materials=publish_materials,
                            batch_id=batch_id,
                        )
                        material_results: List[dict] = result.get("material_results") or []
                except Exception as e:
                    logger.error(
                        f"[定时发布] 规则 #{schedule_id} 第{round_no}轮账号 {aid} 发布异常: {e}"
                    )
                    err = str(e)[:300]
                    material_results = [
                        {
                            "material_id": m["id"],
                            "title": m.get("title", ""),
                            "item_no": m.get("_item_no"),
                            "ok": False,
                            "has_valid_account": True,
                            "success_accounts": 0,
                            "failed_accounts": 1,
                            "account_error_accounts": 0,
                            "accounts": [
                                {"account_id": aid, "status": "failed", "error": err}
                            ],
                        }
                        for m in pick
                    ]

                # ---- 累计该账号本轮结果 ----
                any_valid = False
                round_ok = 0
                last_error: Optional[str] = None
                for mr in material_results:
                    total_attempts += 1
                    entry = ScheduledPublishExecutor._detail_entry(
                        mr, weights_by_account.get(aid, {}).get(mr.get("material_id"))
                    )
                    account_section["materials"].append(entry)
                    if mr.get("has_valid_account"):
                        any_valid = True
                    if mr.get("ok"):
                        round_ok += 1
                        account_ok[aid] = account_ok.get(aid, 0) + 1
                        if mr.get("item_no") is not None:
                            batch_numbers_by_account.setdefault(aid, set()).add(mr["item_no"])
                    else:
                        account_fail[aid] = account_fail.get(aid, 0) + int(
                            mr.get("failed_accounts") or 0
                        ) + int(mr.get("account_error_accounts") or 0)
                        for a in (mr.get("accounts") or []):
                            if a.get("status") != "success" and a.get("error"):
                                last_error = a["error"]

                if publish_mode == "random":
                    account_section["status"] = (
                        "done" if account_ok.get(aid, 0) >= random_count
                        else ("partial" if round_ok > 0 else "failed")
                    )
                else:
                    account_section["status"] = (
                        "done" if round_ok == len(pick)
                        else ("partial" if round_ok > 0 else "failed")
                    )
                round_detail["accounts"].append(account_section)

                # ---- 账号熔断判定（仅随机模式有补发，需要熔断保护）----
                if publish_mode == "random":
                    if not any_valid:
                        dead_accounts[aid] = {
                            "round": round_no,
                            "reason": "账号不存在或无权使用，停止补发",
                            "last_error": last_error,
                        }
                    elif round_ok > 0:
                        fail_streak[aid] = 0
                    else:
                        if last_error and any(
                            mk in last_error for mk in _FATAL_ACCOUNT_ERROR_MARKERS
                        ):
                            dead_accounts[aid] = {
                                "round": round_no,
                                "reason": "命中致命错误码，立即熔断",
                                "last_error": last_error[:300],
                            }
                        else:
                            fail_streak[aid] = fail_streak.get(aid, 0) + 1
                            if fail_streak[aid] >= ACCOUNT_BREAKER_STREAK:
                                dead_accounts[aid] = {
                                    "round": round_no,
                                    "reason": f"连续 {ACCOUNT_BREAKER_STREAK} 轮无素材成功，熔断",
                                    "last_error": last_error[:300] if last_error else None,
                                }

            if round_detail["accounts"]:
                detail_rounds.append(round_detail)

            # 更新批次快照的素材数（进度面板按账号展示的总数）
            from app.services.publish_batch_status_service import PublishBatchStatusService

            cum_material_count = sum(
                len(a.get("materials") or [])
                for r in detail_rounds
                for a in (r.get("accounts") or [])
            )
            await PublishBatchStatusService.update_material_count(batch_id, cum_material_count)

            # 终止条件：本轮无任何账号选到素材（全部池耗尽/被过滤）→ 停止
            if not round_progress:
                logger.info(f"[定时发布] 规则 #{schedule_id} 素材池耗尽，补发结束")
                break

            # 全部达标或全部熔断时，下一轮 active 为空自然退出
            await asyncio.sleep(2)  # 补发轮间隔，避免请求过密

        # ========== 第 3 阶段：汇总与回写执行记录 ==========
        account_result: List[Dict[str, Any]] = []
        for aid in ordered_account_ids:
            ok = account_ok.get(aid, 0)
            if publish_mode != "random":
                status, reason = "done", None
            elif aid in dead_accounts:
                status, reason = "dead", dead_accounts[aid]["reason"]
            elif ok >= random_count:
                status, reason = "done", None
            elif aid in pool_exhausted_accounts:
                status, reason = "pool_exhausted", "素材池耗尽或全部被去重过滤，未达标"
            else:
                status, reason = "incomplete", "未达标"
            account_result.append(
                {
                    "account_id": aid,
                    "target": random_count if publish_mode == "random" else None,
                    "ok": ok,
                    "fail": account_fail.get(aid, 0),
                    "status": status,
                    "reason": reason,
                }
            )

        fail_summary = ScheduledPublishExecutor._build_fail_summary(detail_rounds)
        failed_total = sum(account_fail.values())

        detail_json = ScheduledPublishExecutor._compact_detail({
            "publish_mode": publish_mode,
            "random_count": random_count if publish_mode == "random" else None,
            "deduplicate": deduplicate_enabled,
            "rounds": detail_rounds,
            "filtered": filtered_materials,
            "account_result": account_result,
            "breaker": dead_accounts or None,
            "fail_summary": fail_summary,
        })
        if publish_mode == "random":
            detail_json["weight_algorithm_id"] = weight_algorithm_id
            detail_json["weight_algorithm_name"] = weight_algorithm_name or "热度均衡"
            detail_json["sample_mode"] = weight_params.get("sample_mode")

        # 失败摘要写入执行记录 error_message（列表行直接可见，不点开明细也能看到原因）
        error_message = None
        if failed_total > 0:
            parts = []
            for fs in fail_summary[:5]:
                label = fs.get("account_id") or "账号"
                parts.append(f"{label}×{fs['count']}（{fs.get('code') or fs.get('message', '')[:80]}）")
            error_message = "部分账号发布失败：" + "；".join(parts)[:980]

        await ScheduledPublishExecutor._finalize_log(
            schedule_log_id,
            status="completed",
            success_count=sum(account_ok.values()),
            failed_count=failed_total,
            total_count=total_attempts,
            detail_json=detail_json,
            error_message=error_message,
        )

    # ==================== 内部方法 ====================

    @staticmethod
    def _weighted_sample(
        remaining: List[dict],
        weights_map: Dict[int, int],
        k: int,
        sample_mode: Optional[str] = None,
    ) -> List[dict]:
        """按选料方式取 k 条素材（权重缺省按 1 分保底）。

        weighted（默认）：加权无放回随机——权重=概率，高分不保证必选；
        top：按权重直选——权重降序取前 k（同分保持素材池原顺序）。
        """
        pool_list = list(remaining)
        if sample_mode == "top":
            pool_list.sort(
                key=lambda m: weights_map.get(m.get("id"), 1), reverse=True
            )
            return pool_list[: min(k, len(pool_list))]
        picks: List[dict] = []
        for _ in range(min(k, len(pool_list))):
            weights = [max(weights_map.get(m.get("id"), 1), 1) for m in pool_list]
            idx = random.choices(range(len(pool_list)), weights=weights, k=1)[0]
            picks.append(pool_list.pop(idx))
        return picks

    @staticmethod
    async def _get_algorithm_name(algorithm_id: Optional[int]) -> Optional[str]:
        """取权重算法名称（算法不存在返回 None）"""
        if algorithm_id is None:
            return None
        from common.models.weight_algorithm import WeightAlgorithm

        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
                )
            ).scalar_one_or_none()
            return row.name if row else None

    @staticmethod
    async def _prepare(
        user_id: int,
        schedule_data: Dict[str, Any],
        deduplicate_enabled: bool,
        need_status_numbers: bool = False,
    ) -> Tuple[List[dict], Dict[str, Set[int]], Dict[str, Set[int]]]:
        """加载素材池，并按需刷新账号在售商品列表做去重准备。

        账号远程刷新（昂贵、易限流）仅去重开关开启时执行；本地状态读取
        在去重或硬排已售出任一需要时执行（关去重时状态可能滞后于最近一次同步）。

        Returns:
            (素材池 dict 列表, 账号→在售编号集合, 账号→已售出编号集合)——均按账号独立
        """
        from app.services.item_service import ItemService
        from app.services.product_publish_service import ProductMaterialService, _material_to_dict

        async with async_session_maker() as session:
            mat_svc = ProductMaterialService(session)
            # 素材池解析唯一入口：all=库内全部未删除素材（实时），selected=规则ID快照
            materials = await mat_svc.list_for_schedule(
                list(schedule_data.get("material_ids") or []),
                user_id,
                schedule_data.get("material_scope") or "selected",
            )
            pool: List[dict] = []
            for m in materials:
                d = _material_to_dict(m)
                d["_item_no"] = extract_item_number(d.get("title"))
                pool.append(d)

            if not deduplicate_enabled and not need_status_numbers:
                return pool, {}, {}

            # 加载规则内账号（状态读取需要账号范围；去重开启时逐个远程刷新在售列表）
            account_ids = list(dict.fromkeys(schedule_data.get("account_ids") or []))
            stmt = select(XYAccount).where(
                XYAccount.owner_id == user_id,
                XYAccount.account_id.in_(account_ids),
            )
            accounts = list((await session.execute(stmt)).scalars().all())

            if deduplicate_enabled:
                item_svc = ItemService(session)
                for account in accounts:
                    try:
                        logger.info(f"[定时发布] 去重准备：刷新账号 {account.account_id} 在售商品列表")
                        await item_svc.fetch_all_items_from_account(account=account)
                    except Exception as e:
                        logger.warning(
                            f"[定时发布] 账号 {account.account_id} 刷新在售商品失败（继续去重）: {e}"
                        )

            # 读取商品标题编号（按账号分组：在售硬排除，已售出供算法开关使用）
            on_sale_by_account: Dict[str, Set[int]] = {}
            sold_by_account: Dict[str, Set[int]] = {}
            account_pks = [a.id for a in accounts]
            if account_pks:
                title_stmt = (
                    select(
                        XYCatalogItem.title,
                        XYCatalogItem.metadata_json,
                        XYAccount.account_id,
                    )
                    .join(XYAccount, XYCatalogItem.account_pk == XYAccount.id)
                    .where(
                        XYCatalogItem.owner_id == user_id,
                        XYCatalogItem.account_pk.in_(account_pks),
                    )
                )
                for title, meta, account_id in (await session.execute(title_stmt)).all():
                    num = extract_item_number(title)
                    if num is None:
                        continue
                    status = _normalize_item_status((meta or {}).get("item_status"))
                    if status == "on_sale":
                        on_sale_by_account.setdefault(account_id, set()).add(num)
                    elif status == "sold":
                        sold_by_account.setdefault(account_id, set()).add(num)

            logger.info(
                f"[定时发布] 状态读取完成：{len(pool)} 条素材，"
                f"账号在售编号 {sum(len(v) for v in on_sale_by_account.values())} 个，"
                f"已售出编号 {sum(len(v) for v in sold_by_account.values())} 个"
            )
            return pool, on_sale_by_account, sold_by_account

    @staticmethod
    def _detail_entry(mr: dict, weight: Optional[int] = None) -> dict:
        """将批量发布的素材结果转为执行明细条目（含权重，便于追溯选料原因）"""
        accounts = mr.get("accounts") or []
        if mr.get("ok"):
            result = "success"
        elif mr.get("has_valid_account"):
            result = "failed"
        else:
            result = "account_error"
        entry = {
            "material_id": mr.get("material_id"),
            "title": mr.get("title", ""),
            "item_no": f"A{mr['item_no']}" if mr.get("item_no") is not None else None,
            "result": result,
        }
        if result != "success":
            err = next((a.get("error") for a in accounts if a.get("error")), None)
            if err:
                entry["error"] = str(err)[:300]
        if weight is not None:
            entry["weight"] = weight
        return entry

    @staticmethod
    def _build_fail_summary(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """聚合失败明细：按（账号, 错误码, 原因）分组计数，附通用检查提示。

        只做原文转述：错误码正则提取，原因保留闲鱼 API 原始文案（截断 200），
        不做任何具体数量解读（限额数字以闲鱼返回原文为准）。
        """
        counts: Dict[tuple, int] = {}
        for r in rounds:
            for acc in (r.get("accounts") or []):
                aid = acc.get("account_id")
                for m in (acc.get("materials") or []):
                    if m.get("result") != "failed":
                        continue
                    err = str(m.get("error") or "").strip()
                    if not err:
                        continue
                    code_match = _FAIL_CODE_RE.search(err)
                    code = code_match.group(0) if code_match else None
                    if "::" in err:
                        message = err.rsplit("::", 1)[1].strip() or err
                    else:
                        message = err
                    message = message[:200]
                    counts[(aid, code, message)] = counts.get((aid, code, message), 0) + 1

        summary: List[Dict[str, Any]] = []
        for (aid, code, message), count in sorted(
            counts.items(), key=lambda kv: kv[1], reverse=True
        )[:20]:
            hint = None
            for mk, h in _FAIL_HINT_MARKERS:
                if mk in (code or "") or mk in message:
                    hint = h
                    break
            entry: Dict[str, Any] = {
                "account_id": aid,
                "code": code,
                "message": message,
                "count": count,
            }
            if hint:
                entry["hint"] = hint
            summary.append(entry)
        return summary

    # 素材量过大时，素材×账号明细落库可能超过 MySQL JSON 列/max_allowed_packet 限制，
    # 超出阈值后丢弃素材级明细数组，仅保留每账号的结果计数，保证落库体积可控。
    _MAX_DETAIL_MATERIALS = 500
    _MAX_FILTERED_ENTRIES = 500

    @staticmethod
    def _compact_detail(detail_json: dict) -> dict:
        """压缩执行明细体积，避免超大 detail_json 落库失败"""
        rounds = detail_json.get("rounds") or []
        total_entries = sum(
            len(a.get("materials") or []) for r in rounds for a in (r.get("accounts") or [])
        )
        if total_entries > ScheduledPublishExecutor._MAX_DETAIL_MATERIALS:
            for r in rounds:
                for a in r.get("accounts") or []:
                    materials = a.pop("materials", [])
                    a["material_counts"] = {
                        "success": sum(1 for m in materials if m.get("result") == "success"),
                        "failed": sum(1 for m in materials if m.get("result") == "failed"),
                        "account_error": sum(1 for m in materials if m.get("result") == "account_error"),
                    }
            detail_json["detail_truncated"] = True

        filtered = detail_json.get("filtered") or []
        if len(filtered) > ScheduledPublishExecutor._MAX_FILTERED_ENTRIES:
            detail_json["filtered_count"] = len(filtered)
            detail_json["filtered"] = filtered[: ScheduledPublishExecutor._MAX_FILTERED_ENTRIES]
            detail_json["detail_truncated"] = True
        return detail_json

    @staticmethod
    async def _finalize_log(
        schedule_log_id: int,
        *,
        status: str = "completed",
        success_count: int = 0,
        failed_count: int = 0,
        total_count: int = 0,
        detail_json: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """回写执行记录（含 executed_at 与 detail_json）"""
        try:
            async with async_session_maker() as session:
                stmt = select(PublishScheduleLog).where(
                    PublishScheduleLog.id == schedule_log_id
                )
                log_entry = (await session.execute(stmt)).scalar_one_or_none()
                if log_entry:
                    log_entry.status = status
                    log_entry.executed_at = get_beijing_now()
                    log_entry.success_count = success_count
                    log_entry.failed_count = failed_count
                    if total_count:
                        log_entry.total_count = total_count
                    if detail_json is not None:
                        log_entry.detail_json = detail_json
                    if error_message:
                        log_entry.error_message = error_message[:1000]
                    await session.commit()
                    logger.info(
                        f"[定时发布] 执行记录 #{schedule_log_id} 已更新: "
                        f"status={status}, success={success_count}, failed={failed_count}"
                    )
        except Exception as e:
            logger.error(f"[定时发布] 更新执行记录 #{schedule_log_id} 失败: {e}")
