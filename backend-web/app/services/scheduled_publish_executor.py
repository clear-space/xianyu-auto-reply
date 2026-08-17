"""
定时发布共享执行器

功能（随机选料 / 去重 / 自动补发 逻辑只维护一份，backend 手动触发与 scheduler 定时触发共用）：
1. 指定发布（specified）：发布全部所选素材
2. 随机发布（random）：每次触发从素材池随机选 random_count 条
3. 去重（deduplicate，随机模式可用）：
   - 发布前刷新规则内账号的在售商品列表
   - 按素材标题前缀编号（A+数字）与账号在售商品编号比对，已存在的过滤
   - 素材无编号则不过滤
4. 自动补发：随机模式下本次发布成功数 < random_count 时，从素材池剩余素材中换选补发，直到达标或池耗尽
   - 已尝试过的素材不再选；去重规则同样生效
   - 账号级失败（账号不存在/无权使用）不进入补发
   - 每次补发轮使用独立 DB session
"""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any, Dict, List, Optional, Set

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.publish_schedule_log import PublishScheduleLog
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.utils.time_utils import get_beijing_now

# 素材标题前缀编号：A+数字（如 "A1 手机壳"、标题开头 "A12 ..."）
_ITEM_NO_RE = re.compile(r"^[Aa]\s*(\d+)")


def extract_item_number(title: Optional[str]) -> Optional[int]:
    """从素材/商品标题提取前缀编号（A+数字），无编号返回 None"""
    if not title:
        return None
    match = _ITEM_NO_RE.match(str(title).strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


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
        执行一次定时发布（含随机选料、去重、自动补发），完成后回写执行记录。

        Args:
            user_id: 规则所属用户ID
            schedule_data: 规则快照 dict（id/account_ids/material_ids/publish_mode/random_count/deduplicate_enabled）
            batch_id: 本次执行的批次ID（执行记录与发布日志共用）
            schedule_log_id: 执行记录ID
        """
        from app.services.publish_execution_service import PublishExecutorService

        schedule_id = schedule_data.get("id")
        publish_mode = schedule_data.get("publish_mode") or "specified"
        random_count = int(schedule_data.get("random_count") or 0)
        account_ids = list(schedule_data.get("account_ids") or [])
        deduplicate_enabled = bool(
            schedule_data.get("deduplicate_enabled") and publish_mode == "random"
        )

        # ========== 第 1 阶段：加载素材池 + 去重准备（独立 session） ==========
        try:
            pool, on_sale_numbers = await ScheduledPublishExecutor._prepare(
                user_id, schedule_data, deduplicate_enabled
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

        # ========== 第 2 阶段：发布轮次循环（每轮独立 DB session） ==========
        tried_ids: Set[int] = set()
        batch_numbers: Set[int] = set()  # 本批次内已发布到任一账号的编号（防补发轮选出同编号素材）
        filtered_ids: Set[int] = set()   # 已记录的去重过滤素材（避免多轮重复记录）
        ok_count = 0                     # 素材级成功数（与 random_count 达标比对）
        total_success = 0                # 账号×素材维度成功数（落库用）
        total_failed = 0                 # 账号×素材维度失败数（含账号级失败）
        fatal_error: Optional[str] = None
        detail_rounds: List[Dict[str, Any]] = []
        filtered_materials: List[Dict[str, Any]] = []
        round_no = 0

        while True:
            round_no += 1
            remaining: List[dict] = [m for m in pool if m["id"] not in tried_ids]

            if deduplicate_enabled:
                # 去重过滤：编号已存在于账号在售列表 或 本批次已发布 的素材不再选
                kept: List[dict] = []
                for m in remaining:
                    if (
                        m.get("_item_no") is not None
                        and m["_item_no"] in (on_sale_numbers | batch_numbers)
                    ):
                        if m["id"] not in filtered_ids:
                            filtered_ids.add(m["id"])
                            filtered_materials.append(
                                {
                                    "material_id": m["id"],
                                    "title": m.get("title", ""),
                                    "item_no": f"A{m['_item_no']}",
                                    "round": round_no,
                                }
                            )
                    else:
                        kept.append(m)
                remaining = kept

            if publish_mode == "specified":
                # 指定发布：第一轮发全部，无补发
                pick = remaining if round_no == 1 else []
            else:
                deficit = max(random_count - ok_count, 0)
                pick = (
                    random.sample(remaining, min(deficit, len(remaining)))
                    if remaining and deficit > 0
                    else []
                )

            if not pick:
                break

            logger.info(
                f"[定时发布] 规则 #{schedule_id} 第{round_no}轮发布: {len(pick)} 条素材 × {len(account_ids)} 账号, batch_id={batch_id}"
            )

            try:
                # 每轮独立 DB session（验收要点：session 生命周期）
                async with async_session_maker() as session:
                    svc = PublishExecutorService(session)
                    # 传给发布层前去掉内部辅助字段，避免影响发布 payload
                    publish_materials = [
                        {k: v for k, v in m.items() if k != "_item_no"} for m in pick
                    ]
                    result = await svc.batch_publish(
                        user_id=user_id,
                        account_ids=account_ids,
                        materials=publish_materials,
                        batch_id=batch_id,
                    )
                    material_results: List[dict] = result.get("material_results") or []
            except Exception as e:
                fatal_error = f"第{round_no}轮发布异常: {str(e)[:800]}"
                logger.error(f"[定时发布] 规则 #{schedule_id} {fatal_error}")
                break

            # 累计本轮结果
            round_detail: Dict[str, Any] = {"round": round_no, "materials": []}
            any_valid_account = False
            for mr in material_results:
                tried_ids.add(mr.get("material_id"))
                if mr.get("has_valid_account"):
                    any_valid_account = True
                if mr.get("ok"):
                    ok_count += 1
                    if mr.get("item_no") is not None:
                        batch_numbers.add(mr.get("item_no"))
                total_success += int(mr.get("success_accounts") or 0)
                total_failed += int(mr.get("failed_accounts") or 0) + int(
                    mr.get("account_error_accounts") or 0
                )
                round_detail["materials"].append(
                    ScheduledPublishExecutor._detail_entry(mr)
                )
            detail_rounds.append(round_detail)

            # 更新批次快照的素材数（进度面板按账号展示的总数）
            from app.services.publish_batch_status_service import PublishBatchStatusService

            cum_material_count = sum(len(r["materials"]) for r in detail_rounds)
            await PublishBatchStatusService.update_material_count(batch_id, cum_material_count)

            # 终止条件
            if publish_mode != "random":
                break
            if ok_count >= random_count:
                logger.info(
                    f"[定时发布] 规则 #{schedule_id} 达标（成功 {ok_count}/{random_count}），补发结束"
                )
                break
            if not any_valid_account:
                # 所有账号都失败（账号不存在/无权使用），补发无意义
                logger.warning(f"[定时发布] 规则 #{schedule_id} 账号级全失败，补发无意义")
                break
            if len(tried_ids) >= len(pool):
                logger.info(f"[定时发布] 规则 #{schedule_id} 素材池耗尽，补发结束")
                break

            await asyncio.sleep(2)  # 补发轮间隔，避免请求过密

        # ========== 第 3 阶段：回写执行记录 ==========
        total_count = sum(
            len(r["materials"]) * len(account_ids) for r in detail_rounds if r["materials"]
        ) if account_ids else 0

        detail_json = ScheduledPublishExecutor._compact_detail({
            "publish_mode": publish_mode,
            "random_count": random_count if publish_mode == "random" else None,
            "deduplicate": deduplicate_enabled,
            "target_ok": ok_count,
            "rounds": detail_rounds,
            "filtered": filtered_materials,
        })

        if fatal_error:
            await ScheduledPublishExecutor._finalize_log(
                schedule_log_id,
                status="failed",
                success_count=total_success,
                failed_count=total_failed,
                total_count=total_count,
                detail_json=detail_json,
                error_message=fatal_error,
            )
        else:
            await ScheduledPublishExecutor._finalize_log(
                schedule_log_id,
                status="completed",
                success_count=total_success,
                failed_count=total_failed,
                total_count=total_count,
                detail_json=detail_json,
            )

    # ==================== 内部方法 ====================

    @staticmethod
    async def _prepare(
        user_id: int, schedule_data: Dict[str, Any], deduplicate_enabled: bool
    ) -> tuple[List[dict], Set[int]]:
        """加载素材池，并按需刷新账号在售商品列表做去重准备。

        Returns:
            (素材池 dict 列表, 在售商品编号集合)
        """
        from app.services.item_service import ItemService
        from app.services.product_publish_service import ProductMaterialService, _material_to_dict

        async with async_session_maker() as session:
            mat_svc = ProductMaterialService(session)
            materials = await mat_svc.list_by_ids(
                list(schedule_data.get("material_ids") or []), user_id
            )
            pool: List[dict] = []
            for m in materials:
                d = _material_to_dict(m)
                d["_item_no"] = extract_item_number(d.get("title"))
                pool.append(d)

            if not deduplicate_enabled:
                return pool, set()

            # 去重准备：加载规则内账号，逐个刷新在售商品列表（失败不阻断发布，仅降级为不去重）
            account_ids = list(dict.fromkeys(schedule_data.get("account_ids") or []))
            stmt = select(XYAccount).where(
                XYAccount.owner_id == user_id,
                XYAccount.account_id.in_(account_ids),
            )
            accounts = list((await session.execute(stmt)).scalars().all())

            item_svc = ItemService(session)
            for account in accounts:
                try:
                    logger.info(f"[定时发布] 去重准备：刷新账号 {account.account_id} 在售商品列表")
                    await item_svc.fetch_all_items_from_account(account=account)
                except Exception as e:
                    logger.warning(
                        f"[定时发布] 账号 {account.account_id} 刷新在售商品失败（继续去重）: {e}"
                    )

            # 读取刷新后的在售商品标题编号（union：任一账号在售即视为已存在）
            on_sale_numbers: Set[int] = set()
            account_pks = [a.id for a in accounts]
            if account_pks:
                title_stmt = select(XYCatalogItem.title).where(
                    XYCatalogItem.owner_id == user_id,
                    XYCatalogItem.account_pk.in_(account_pks),
                )
                for (title,) in (await session.execute(title_stmt)).all():
                    num = extract_item_number(title)
                    if num is not None:
                        on_sale_numbers.add(num)

            logger.info(
                f"[定时发布] 去重准备完成：{len(pool)} 条素材，账号在售编号 {len(on_sale_numbers)} 个"
            )
            return pool, on_sale_numbers

    @staticmethod
    def _detail_entry(mr: dict) -> dict:
        """将批量发布的素材结果转为执行明细条目"""
        if mr.get("ok"):
            result = "success"
        elif mr.get("has_valid_account"):
            result = "failed"
        else:
            result = "account_error"
        return {
            "material_id": mr.get("material_id"),
            "title": mr.get("title", ""),
            "item_no": f"A{mr['item_no']}" if mr.get("item_no") is not None else None,
            "result": result,
            "accounts": mr.get("accounts") or [],
        }

    # 素材量过大时，素材×账号明细落库可能超过 MySQL JSON 列/max_allowed_packet 限制，
    # 超出阈值后丢弃账号级明细数组，仅保留每素材的账号结果计数，保证落库体积可控。
    _MAX_DETAIL_MATERIALS = 500
    _MAX_FILTERED_ENTRIES = 500

    @staticmethod
    def _compact_detail(detail_json: dict) -> dict:
        """压缩执行明细体积，避免超大 detail_json 落库失败"""
        rounds = detail_json.get("rounds") or []
        total_entries = sum(len(r.get("materials") or []) for r in rounds)
        if total_entries > ScheduledPublishExecutor._MAX_DETAIL_MATERIALS:
            for r in rounds:
                for m in r.get("materials") or []:
                    accounts = m.pop("accounts", [])
                    m["account_counts"] = {
                        "success": sum(1 for a in accounts if a.get("status") == "success"),
                        "failed": sum(1 for a in accounts if a.get("status") == "failed"),
                        "account_error": sum(1 for a in accounts if a.get("status") == "account_error"),
                    }
            detail_json["detail_truncated"] = True

        filtered = detail_json.get("filtered") or []
        if len(filtered) > ScheduledPublishExecutor._MAX_FILTERED_ENTRIES:
            detail_json["filtered_count"] = len(filtered)
            detail_json["filtered"] = filtered[:ScheduledPublishExecutor._MAX_FILTERED_ENTRIES]
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
