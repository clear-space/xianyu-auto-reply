"""
自动下架共享执行器

功能（筛选 / 分组下架 / 删本地 逻辑只维护一份，backend 手动触发与 scheduler 定时触发共用）：
1. 筛选：仅规则内账号的在售商品（账号维度过滤 + 在售状态，非在售下架接口必败）；
   处于下架失败冷却期（DELIST_FAIL_COOLDOWN_SECONDS 内失败过）的商品跳过，
   避免重试反复选中同一批被闲鱼拒绝的商品
2. 选品：下架权重算法打分（真实上架天数、近7天曝光/浏览/咨询/成交/转化率、
   累计想要、擦亮官方数据归一化加权，无快照商品不参与），
   按权重降序（top 直选）或加权随机取每个账号前 Z 个
3. 执行：按账号分组，每个账号一次批量下架 API（mtop batch.offline）；
   账号无 Cookie/不存在 → 跳过并记账号级失败；
   失败商品写入 metadata（delist_fail_at/delist_fail_error）进入冷却
4. 成功下架后本地标记 offline + offline_at（保留记录，供上架权重恢复信号），并清除冷却标记
5. 无符合条件的商品：写 total_count=0 的 completed 记录（不静默跳过）
6. 每次执行后回写执行记录（executed_at + detail_json，明细过大自动压缩）；
   失败原因聚合为 fail_summary（错误码 + 原始原因 + 次数）供定时历史展示
"""
from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.offline_schedule_log import OfflineScheduleLog
from common.services.delist_scoring import DEFAULT_DELIST_PARAMS, get_delist_algorithm_params
from common.utils.time_utils import get_beijing_now

# 下架失败冷却：失败商品在冷却期内不再被任何下架规则选中（单位秒）
DELIST_FAIL_COOLDOWN_SECONDS = 6 * 3600

# 商品 metadata 冷却键：delist_fail_at（ISO 时间）、delist_fail_error（≤200 字）
_META_FAIL_AT = "delist_fail_at"
_META_FAIL_ERROR = "delist_fail_error"

# 失败原因里的错误码提取（闲鱼 mtop 业务错误码形如 FAIL_XXX_NEW）
_FAIL_CODE_RE = re.compile(r"FAIL_[A-Z0-9_]+")

# 已知失败原因的通用检查提示（不做具体数量解读，仅提醒人工检查）
_FAIL_HINT_MARKERS = (
    ("商品批量下架处理异常", "请检查该账号商品状态与登录态"),
    ("FAIL_SYS_TOKEN", "请检查该账号登录态"),
)


class OfflineExecutor:
    """自动下架执行器：手动触发路由与内部端点（scheduler 调用）共用同一份实现"""

    @staticmethod
    async def run(
        user_id: int,
        schedule_data: Dict[str, Any],
        batch_id: str,
        schedule_log_id: int,
    ) -> None:
        """
        执行一次自动下架（筛选 → 分组下架 → 删本地 → 回写执行记录）。

        Args:
            user_id: 规则所属用户ID
            schedule_data: 规则快照 dict（id/account_ids/max_count/delist_algorithm_id）
            batch_id: 本次执行的批次ID
            schedule_log_id: 执行记录ID
        """
        schedule_id = schedule_data.get("id")
        account_ids = list(schedule_data.get("account_ids") or [])
        max_count = int(schedule_data.get("max_count") or 1)
        delist_algorithm_id = schedule_data.get("delist_algorithm_id")

        # 下架权重算法参数（算法停用/缺失自动回退系统默认）
        delist_algorithm_name = None
        delist_params = DEFAULT_DELIST_PARAMS
        try:
            delist_params = await get_delist_algorithm_params(delist_algorithm_id)
            delist_algorithm_name = await OfflineExecutor._get_algorithm_name(
                delist_algorithm_id
            )
        except Exception as exc:
            logger.warning(
                f"[定时下架] 规则 #{schedule_id} 加载下架权重算法失败（回退下架均衡）: {exc}"
            )

        # ========== 第 1 阶段：筛选目标商品（独立 session） ==========
        try:
            targets, account_map, missing_account_ids, cooldown_counts = (
                await OfflineExecutor._select_targets(
                    user_id, account_ids, max_count, delist_params
                )
            )
        except Exception as e:
            logger.error(f"[定时下架] 规则 #{schedule_id} 筛选阶段失败: {e}")
            await OfflineExecutor._finalize_log(
                schedule_log_id,
                status="failed",
                error_message=f"筛选阶段失败: {str(e)[:800]}",
                detail_json={
                    "max_count": max_count,
                    "sample_mode": delist_params.get("sample_mode"),
                    "algorithm_id": delist_algorithm_id,
                    "algorithm_name": delist_algorithm_name or "系统默认",
                    "accounts": [],
                    "missing_accounts": [],
                },
            )
            return

        # ========== 第 2 阶段：逐账号下架 + 删本地（每账号独立 session） ==========
        detail_accounts: List[Dict[str, Any]] = []
        success_total = 0
        failed_total = 0
        fatal_error: Optional[str] = None
        ordered_account_ids = list(dict.fromkeys(account_ids))

        for account_id in ordered_account_ids:
            items = targets.get(account_id, [])
            if not items:
                continue
            entry: Dict[str, Any] = {
                "account_id": account_id,
                "status": "success",
                "suc_count": 0,
                "fail_count": 0,
                "items": [],
            }
            account = account_map.get(account_id)
            if account is None or not (account.cookie or "").strip():
                entry["status"] = "account_error"
                entry["error"] = "账号不存在或缺少Cookie，跳过下架"
                entry["items"] = [
                    OfflineExecutor._detail_item_entry(it, "skipped") for it in items
                ]
                failed_total += len(items)
                detail_accounts.append(entry)
                continue

            try:
                async with async_session_maker() as session:
                    from common.models.xy_account import XYAccount
                    from common.services.item_offline_service import batch_offline_items_from_xianyu

                    # 在本 session 内重新加载账号对象（本地删除依赖归属校验）
                    acc_stmt = select(XYAccount).where(
                        XYAccount.owner_id == user_id,
                        XYAccount.account_id == account_id,
                    )
                    acc_row = (await session.execute(acc_stmt)).scalar_one_or_none()
                    if acc_row is None or not (acc_row.cookie or "").strip():
                        entry["status"] = "account_error"
                        entry["error"] = "账号不存在或缺少Cookie，跳过下架"
                        entry["items"] = [
                            OfflineExecutor._detail_item_entry(it, "skipped") for it in items
                        ]
                        failed_total += len(items)
                        detail_accounts.append(entry)
                        continue

                    item_ids = [it["item"].item_id for it in items]
                    logger.info(
                        f"[定时下架] 规则 #{schedule_id} 账号 {account_id} 批量下架 {len(item_ids)} 个商品"
                    )
                    result = await batch_offline_items_from_xianyu(
                        account_id=account_id,
                        cookies_str=acc_row.cookie,
                        item_ids=item_ids,
                    )

                    ok_map = {
                        str(r.get("item_id")): bool(r.get("success"))
                        for r in (result.get("results") or [])
                    }
                    failed_item_ids: List[str] = []
                    for it in items:
                        item_id = it["item"].item_id
                        if ok_map.get(item_id, False):
                            # 下架成功：本地记录保留，标记 offline + offline_at（权重恢复信号来源），
                            # 并清除失败冷却标记
                            marked = await OfflineExecutor._mark_item_offline(
                                session, acc_row, item_id
                            )
                            entry["suc_count"] += 1
                            success_total += 1
                            note = None if marked else "远程已下架，本地记录已不存在"
                            entry["items"].append(
                                OfflineExecutor._detail_item_entry(
                                    it, "success", note=note
                                )
                            )
                        else:
                            entry["fail_count"] += 1
                            failed_total += 1
                            failed_item_ids.append(item_id)
                            entry["items"].append(
                                OfflineExecutor._detail_item_entry(
                                    it, "failed", error=result.get("message") or "下架失败"
                                )
                            )

                    # 失败商品进入冷却（写 metadata，单次 commit），避免重试反复选中同一批
                    if failed_item_ids:
                        try:
                            await OfflineExecutor._mark_items_delist_failed(
                                session, acc_row, failed_item_ids,
                                result.get("message") or "下架失败",
                            )
                        except Exception as mark_exc:
                            logger.error(
                                f"[定时下架] 账号 {account_id} 失败冷却标记异常: {mark_exc}"
                            )

                    # 整批被拒：账号级错误写入 entry，日志/明细可一眼区分
                    # 「账号级整批拒绝」与「个别商品失败」
                    if entry["suc_count"] == 0 and entry["fail_count"] == len(items) and items:
                        entry["error"] = (
                            result.get("message") or "整批被闲鱼拒绝"
                        )[:300]

                if entry["fail_count"] > 0:
                    entry["status"] = "partial" if entry["suc_count"] > 0 else "failed"
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = f"下架异常: {str(e)[:300]}"
                entry["items"] = [
                    OfflineExecutor._detail_item_entry(
                        it, "failed", error=str(e)[:200]
                    )
                    for it in items
                ]
                failed_total += len(items)
                logger.error(
                    f"[定时下架] 规则 #{schedule_id} 账号 {account_id} 下架异常: {e}"
                )

            detail_accounts.append(entry)

        # ========== 第 3 阶段：回写执行记录 ==========
        total_count = sum(len(v) for v in targets.values())
        fail_summary = OfflineExecutor._build_fail_summary(detail_accounts)

        detail_json = OfflineExecutor._compact_detail({
            "max_count": max_count,
            "sample_mode": delist_params.get("sample_mode"),
            "algorithm_id": delist_algorithm_id,
            "algorithm_name": delist_algorithm_name or "系统默认",
            "accounts": detail_accounts,
            "missing_accounts": missing_account_ids,
            "cooldown_skipped": cooldown_counts or None,
            "fail_summary": fail_summary,
        })

        # 失败摘要写入执行记录 error_message（列表行直接可见，不点开明细也能看到原因）
        error_message = None
        if failed_total > 0:
            parts = []
            for fs in fail_summary[:5]:
                label = fs.get("account_id") or "账号"
                parts.append(f"{label}×{fs['count']}（{fs.get('code') or fs.get('message', '')[:80]}）")
            error_message = "部分账号下架失败：" + "；".join(parts)[:980]

        if fatal_error:
            await OfflineExecutor._finalize_log(
                schedule_log_id,
                status="failed",
                success_count=success_total,
                failed_count=failed_total,
                total_count=total_count,
                detail_json=detail_json,
                error_message=fatal_error,
            )
        else:
            # 无符合条件的商品也写 completed（total_count=0，不静默跳过）
            await OfflineExecutor._finalize_log(
                schedule_log_id,
                status="completed",
                success_count=success_total,
                failed_count=failed_total,
                total_count=total_count,
                detail_json=detail_json,
                error_message=error_message,
            )

    # ==================== 内部方法 ====================

    @staticmethod
    async def _select_targets(
        user_id: int,
        account_ids: List[str],
        max_count: int,
        delist_params: Dict[str, Any],
    ) -> Tuple[Dict[str, list], Dict[str, Any], List[str], Dict[str, int]]:
        """按算法权重筛选每个账号待下架的商品（权重降序，每账号最多 max_count 个）。

        下架失败冷却期内的商品（metadata.delist_fail_at 距今 < DELIST_FAIL_COOLDOWN_SECONDS）
        不参与选品，避免重试反复选中同一批被闲鱼拒绝的商品。

        Returns:
            (account_id -> 打分明细列表[{item, weight, signals, parts}], account_id -> 账号对象,
             规则中不存在/无权的账号ID列表, account_id -> 冷却跳过商品数)
        """
        from common.models.xy_account import XYAccount
        from common.models.xy_catalog_item import XYCatalogItem
        from common.services.delist_scoring import compute_delist_scores
        from common.services.item_service import _normalize_item_status

        async with async_session_maker() as session:
            unique_ids = list(dict.fromkeys(account_ids))
            stmt = select(XYAccount).where(
                XYAccount.owner_id == user_id,
                XYAccount.account_id.in_(unique_ids),
            )
            accounts = list((await session.execute(stmt)).scalars().all())
            account_map = {a.account_id: a for a in accounts}
            missing = [aid for aid in unique_ids if aid not in account_map]

            targets: Dict[str, list] = {}
            cooldown_counts: Dict[str, int] = {}
            for account in accounts:
                item_stmt = select(XYCatalogItem).where(
                    XYCatalogItem.owner_id == user_id,
                    XYCatalogItem.account_pk == account.id,
                )
                rows = list((await session.execute(item_stmt)).scalars().all())

                # 只在售商品参与下架（已售出/已下架/已删除/已失效/未知状态均跳过，
                # 否则下架接口对非在售商品必然失败）；失败冷却期内的商品也跳过
                on_sale_rows = []
                for r in rows:
                    meta = r.metadata_json or {}
                    if _normalize_item_status(meta.get("item_status")) != "on_sale":
                        continue
                    if OfflineExecutor._in_delist_cooldown(r):
                        cooldown_counts[account.account_id] = (
                            cooldown_counts.get(account.account_id, 0) + 1
                        )
                        continue
                    on_sale_rows.append(r)
                if not on_sale_rows:
                    targets[account.account_id] = []
                    continue

                scored = await compute_delist_scores(
                    user_id,
                    account.account_id,
                    on_sale_rows,
                    params=delist_params,
                    session=session,
                )

                # 硬排开关与无数据商品在评分阶段已置权重 0（signals.excluded/no_data），
                # 此处按权重 > 0 剔除，保证不参与下架；min_score 阈值过滤保持旧语义
                scored = [s for s in scored if s["weight"] > 0]
                min_score = int(delist_params.get("min_score") or 0)
                if min_score > 0:
                    scored = [s for s in scored if s["weight"] >= min_score]

                # 选取：top 按权重直选；weighted 加权无放回随机（权重=概率）
                if delist_params.get("sample_mode") == "weighted":
                    targets[account.account_id] = OfflineExecutor._weighted_sample_scored(
                        scored, max_count
                    )
                else:
                    targets[account.account_id] = scored[:max_count]

            logger.info(
                f"[定时下架] 筛选完成: {len(accounts)} 个账号, "
                f"候选 {sum(len(v) for v in targets.values())} 个商品, "
                f"冷却跳过 {sum(cooldown_counts.values())} 个, "
                f"缺失账号 {len(missing)} 个"
            )
            return targets, account_map, missing, cooldown_counts

    @staticmethod
    def _weighted_sample_scored(
        scored: List[Dict[str, Any]], k: int
    ) -> List[Dict[str, Any]]:
        """加权无放回随机取 k 条（权重=概率，高分不保证必选）"""
        pool = list(scored)
        picks: List[Dict[str, Any]] = []
        for _ in range(min(k, len(pool))):
            weights = [max(s["weight"], 1) for s in pool]
            idx = random.choices(range(len(pool)), weights=weights, k=1)[0]
            picks.append(pool.pop(idx))
        return picks

    @staticmethod
    def _detail_item_entry(
        it: Dict[str, Any],
        result: str,
        error: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        """将打分明细转为执行明细条目（含编号/标题/权重，便于追溯选品原因）"""
        from common.services.material_scoring import extract_prefix_number

        row = it["item"]
        title = row.title or ""
        num = extract_prefix_number(title)
        entry: Dict[str, Any] = {
            "item_id": row.item_id,
            "title": title,
            "item_no": f"A{num}" if num is not None else None,
            "weight": it["weight"],
            "result": result,
        }
        if error:
            entry["error"] = error
        if note:
            entry["note"] = note
        return entry

    @staticmethod
    async def _get_algorithm_name(algorithm_id: Optional[int]) -> Optional[str]:
        """取下架权重算法名称（算法不存在返回 None）"""
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

    # 明细过大时丢弃商品级明细数组，仅保留账号级计数，保证落库体积可控
    _MAX_DETAIL_ITEMS = 500

    @staticmethod
    async def _mark_item_offline(session, account, item_id: str) -> bool:
        """下架成功后本地标记 offline + offline_at（保留记录，供权重恢复信号）"""
        from sqlalchemy.orm.attributes import flag_modified

        from common.models.xy_catalog_item import XYCatalogItem
        from common.utils.time_utils import get_beijing_now

        stmt = select(XYCatalogItem).where(
            XYCatalogItem.owner_id == account.owner_id,
            XYCatalogItem.account_pk == account.id,
            XYCatalogItem.item_id == item_id,
        )
        obj = (await session.execute(stmt)).scalar_one_or_none()
        if obj is None:
            return False
        meta = dict(obj.metadata_json or {})
        meta["item_status"] = "offline"
        meta["offline_at"] = get_beijing_now().isoformat()
        # 下架成功：清除失败冷却标记
        meta.pop(_META_FAIL_AT, None)
        meta.pop(_META_FAIL_ERROR, None)
        obj.metadata_json = meta
        flag_modified(obj, "metadata_json")
        await session.commit()
        return True

    @staticmethod
    def _in_delist_cooldown(row) -> bool:
        """判断商品是否处于下架失败冷却期（metadata.delist_fail_at 距今未满冷却时长）"""
        meta = row.metadata_json or {}
        fail_at = meta.get(_META_FAIL_AT)
        if not fail_at:
            return False
        try:
            ts = datetime.fromisoformat(str(fail_at))
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
        except ValueError:
            return False
        now_naive = get_beijing_now().replace(tzinfo=None)
        return (now_naive - ts).total_seconds() < DELIST_FAIL_COOLDOWN_SECONDS

    @staticmethod
    async def _mark_items_delist_failed(
        session, account, item_ids: List[str], error: str
    ) -> None:
        """失败商品写入冷却标记（delist_fail_at/delist_fail_error），单次 commit"""
        from sqlalchemy.orm.attributes import flag_modified

        from common.models.xy_catalog_item import XYCatalogItem

        if not item_ids:
            return
        stmt = select(XYCatalogItem).where(
            XYCatalogItem.owner_id == account.owner_id,
            XYCatalogItem.account_pk == account.id,
            XYCatalogItem.item_id.in_(list(dict.fromkeys(item_ids))),
        )
        rows = list((await session.execute(stmt)).scalars().all())
        now_iso = get_beijing_now().isoformat()
        err_short = (error or "下架失败")[:200]
        for obj in rows:
            meta = dict(obj.metadata_json or {})
            meta[_META_FAIL_AT] = now_iso
            meta[_META_FAIL_ERROR] = err_short
            obj.metadata_json = meta
            flag_modified(obj, "metadata_json")
        if rows:
            await session.commit()

    @staticmethod
    def _build_fail_summary(detail_accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """聚合失败明细：按（账号, 错误码, 原因）分组计数，附通用检查提示。

        只做原文转述：错误码正则提取，原因保留闲鱼 API 原始文案（截断 200），
        不做任何具体数量解读。
        """
        counts: Dict[tuple, int] = {}
        for acc in detail_accounts:
            aid = acc.get("account_id")
            for it in (acc.get("items") or []):
                if it.get("result") != "failed":
                    continue
                err = str(it.get("error") or "").strip()
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

    @staticmethod
    def _compact_detail(detail_json: dict) -> dict:
        """压缩执行明细体积，避免超大 detail_json 落库失败"""
        accounts = detail_json.get("accounts") or []
        total_items = sum(len(a.get("items") or []) for a in accounts)
        if total_items > OfflineExecutor._MAX_DETAIL_ITEMS:
            for a in accounts:
                a.pop("items", None)
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
                stmt = select(OfflineScheduleLog).where(
                    OfflineScheduleLog.id == schedule_log_id
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
                        f"[定时下架] 执行记录 #{schedule_log_id} 已更新: "
                        f"status={status}, success={success_count}, failed={failed_count}"
                    )
        except Exception as e:
            logger.error(f"[定时下架] 更新执行记录 #{schedule_log_id} 失败: {e}")
