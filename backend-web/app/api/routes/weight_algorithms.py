"""
权重算法管理路由（管理员）

功能：
1. 权重算法 CRUD（管理员集中定义调参规则，上架热度加权 / 下架加权两种类型）
2. 引用计数与删除保护（被定时发布/定时下架规则引用的算法禁止删除）
3. 规则表单通过接口读取启用中的算法
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models.offline_schedule import OfflineSchedule
from common.models.publish_schedule import PublishSchedule
from common.models.user import User
from common.models.weight_algorithm import WeightAlgorithm
from common.schemas.common import ApiResponse
from common.services.delist_scoring import DEFAULT_DELIST_PARAMS, normalize_delist_params
from common.services.material_scoring import DEFAULT_WEIGHT_PARAMS, normalize_weight_params
from common.utils.time_utils import safe_isoformat

router = APIRouter(tags=["权重算法"])

# 算法类型合法值
ALGORITHM_TYPES = ("heat_weight", "delist_weight")

# 内置算法各类型可调参数键（其余参数只读）
_BUILTIN_EDITABLE_KEYS = {
    "heat_weight": ("exclude_sold", "sample_mode"),
    "delist_weight": ("sample_mode",),
}


def _normalize_params_by_type(algorithm_type: str, raw: Optional[dict]) -> dict:
    """按算法类型归一化参数（白名单合并 + 非法值回退默认）"""
    if algorithm_type == "delist_weight":
        return normalize_delist_params(raw)
    return normalize_weight_params(raw)


def _to_dict(algo: WeightAlgorithm, ref_count: int = 0) -> dict:
    return {
        "id": algo.id,
        "name": algo.name,
        "algorithm_type": algo.algorithm_type,
        "description": algo.description,
        "params": _normalize_params_by_type(algo.algorithm_type, algo.params),
        "enabled": bool(algo.enabled),
        "is_builtin": bool(algo.is_builtin),
        "ref_count": ref_count,
        "created_at": safe_isoformat(algo.created_at),
        "updated_at": safe_isoformat(algo.updated_at),
    }


class WeightAlgorithmCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="算法名称")
    algorithm_type: str = Field("heat_weight", max_length=32, description="算法类型：heat_weight-热度加权, delist_weight-下架加权")
    description: Optional[str] = Field(None, max_length=500, description="算法说明")
    params: Dict[str, Any] = Field(..., description="权重参数")


class WeightAlgorithmUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    algorithm_type: Optional[str] = Field(None, max_length=32)
    description: Optional[str] = Field(None, max_length=500)
    params: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


@router.get("", response_model=ApiResponse)
async def list_weight_algorithms(
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """权重算法列表（含引用计数）"""
    rows = list(
        (
            await session.execute(
                select(WeightAlgorithm).order_by(
                    WeightAlgorithm.is_builtin.desc(), WeightAlgorithm.id
                )
            )
        ).scalars().all()
    )
    ref_stmt = (
        select(PublishSchedule.weight_algorithm_id, func.count())
        .where(PublishSchedule.weight_algorithm_id.isnot(None))
        .group_by(PublishSchedule.weight_algorithm_id)
    )
    ref_map = {row[0]: row[1] for row in (await session.execute(ref_stmt)).all()}
    # 下架规则引用合并（上架/下架算法同表存储，引用计数统一）
    delist_ref_stmt = (
        select(OfflineSchedule.delist_algorithm_id, func.count())
        .where(OfflineSchedule.delist_algorithm_id.isnot(None))
        .group_by(OfflineSchedule.delist_algorithm_id)
    )
    for row in (await session.execute(delist_ref_stmt)).all():
        ref_map[row[0]] = ref_map.get(row[0], 0) + row[1]
    return ApiResponse(
        success=True,
        message="查询成功",
        data={
            "list": [_to_dict(a, int(ref_map.get(a.id, 0))) for a in rows],
            "default_params": DEFAULT_WEIGHT_PARAMS,
            "default_delist_params": DEFAULT_DELIST_PARAMS,
        },
    )


@router.post("", response_model=ApiResponse)
async def create_weight_algorithm(
    payload: WeightAlgorithmCreate,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """新建权重算法"""
    exists = (
        await session.execute(
            select(WeightAlgorithm.id).where(WeightAlgorithm.name == payload.name.strip())
        )
    ).scalar_one_or_none()
    if exists:
        return ApiResponse(success=False, message="算法名称已存在")

    algo_type = payload.algorithm_type or "heat_weight"
    if algo_type not in ALGORITHM_TYPES:
        return ApiResponse(success=False, message=f"不支持的算法类型: {algo_type}")

    algo = WeightAlgorithm(
        name=payload.name.strip(),
        algorithm_type=algo_type,
        description=payload.description,
        params=_normalize_params_by_type(algo_type, payload.params),
        enabled=True,
    )
    session.add(algo)
    await session.commit()
    await session.refresh(algo)
    return ApiResponse(success=True, message="算法创建成功", data=_to_dict(algo))


@router.put("/{algorithm_id}", response_model=ApiResponse)
async def update_weight_algorithm(
    algorithm_id: int,
    payload: WeightAlgorithmUpdate,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """更新权重算法"""
    algo = (
        await session.execute(
            select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
        )
    ).scalar_one_or_none()
    if algo is None:
        return ApiResponse(success=False, message="算法不存在")
    if algo.is_builtin:
        # 内置算法：仅允许调整选取方式，其余字段与参数保持只读（静默忽略）
        if payload.params is not None:
            editable = _BUILTIN_EDITABLE_KEYS.get(algo.algorithm_type, ("sample_mode",))
            normalized = _normalize_params_by_type(algo.algorithm_type, payload.params)
            merged = dict(algo.params or {})
            for key in editable:
                merged[key] = normalized.get(key)
            algo.params = merged
            await session.commit()
            await session.refresh(algo)
        return ApiResponse(
            success=True,
            message="内置算法已更新（仅选取方式生效，其余参数只读）",
            data=_to_dict(algo),
        )

    if payload.name is not None:
        name = payload.name.strip()
        exists = (
            await session.execute(
                select(WeightAlgorithm.id).where(
                    WeightAlgorithm.name == name,
                    WeightAlgorithm.id != algorithm_id,
                )
            )
        ).scalar_one_or_none()
        if exists:
            return ApiResponse(success=False, message="算法名称已存在")
        algo.name = name
    if payload.algorithm_type is not None:
        if payload.algorithm_type not in ALGORITHM_TYPES:
            return ApiResponse(success=False, message=f"不支持的算法类型: {payload.algorithm_type}")
        algo.algorithm_type = payload.algorithm_type
    if payload.description is not None:
        algo.description = payload.description
    if payload.params is not None:
        algo.params = _normalize_params_by_type(algo.algorithm_type, payload.params)
    if payload.enabled is not None:
        algo.enabled = payload.enabled

    await session.commit()
    await session.refresh(algo)
    return ApiResponse(success=True, message="算法更新成功", data=_to_dict(algo))


@router.delete("/{algorithm_id}", response_model=ApiResponse)
async def delete_weight_algorithm(
    algorithm_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """删除权重算法（被定时发布/定时下架规则引用时禁止删除）"""
    algo = (
        await session.execute(
            select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
        )
    ).scalar_one_or_none()
    if algo is None:
        return ApiResponse(success=False, message="算法不存在")
    if algo.is_builtin:
        return ApiResponse(success=False, message="系统内置算法不可删除")

    ref_count = (
        await session.execute(
            select(func.count())
            .select_from(PublishSchedule)
            .where(PublishSchedule.weight_algorithm_id == algorithm_id)
        )
    ).scalar() or 0
    ref_count += (
        await session.execute(
            select(func.count())
            .select_from(OfflineSchedule)
            .where(OfflineSchedule.delist_algorithm_id == algorithm_id)
        )
    ).scalar() or 0
    if ref_count > 0:
        return ApiResponse(
            success=False,
            message=f"该算法被 {ref_count} 条规则引用，无法删除（可先停用）",
        )

    await session.delete(algo)
    await session.commit()
    return ApiResponse(success=True, message="算法已删除")


@router.get("/{algorithm_id}/preview", response_model=ApiResponse)
async def preview_weight_algorithm(
    algorithm_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
    account_ids: Optional[str] = Query(None, description="逗号分隔的账号ID列表（下架加权用），不传=全部账号"),
    refresh: bool = Query(False, description="预览前先从闲鱼同步最新商品（下架加权用，较慢）"),
) -> dict:
    """预览算法效果：热度加权对全部素材、下架加权对全部在售商品计算权重，附信号明细与逐项分值"""
    from app.services.product_publish_service import ProductMaterialService
    from common.services.material_scoring import (
        compute_material_weight_details,
        extract_prefix_number,
    )

    algo = (
        await session.execute(
            select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
        )
    ).scalar_one_or_none()
    if algo is None:
        return ApiResponse(success=False, message="算法不存在")

    # 下架加权：对作用域内全部在售商品打分（按账号分组，官方快照信号按账号隔离）
    if algo.algorithm_type == "delist_weight":
        from common.models.xy_account import XYAccount
        from common.models.xy_catalog_item import XYCatalogItem
        from common.services.delist_scoring import compute_delist_scores
        from common.services.item_service import _normalize_item_status
        from common.utils.auth_scope import resolve_owner_scope

        # 与前端账号选择器（/cookies/options）一致的数据作用域：管理员=全部用户，普通用户=仅本人
        scope_owner, _ = resolve_owner_scope(current_user)

        acc_stmt = select(XYAccount)
        if scope_owner is not None:
            acc_stmt = acc_stmt.where(XYAccount.owner_id == scope_owner)
        acc_rows = (await session.execute(acc_stmt)).scalars().all()
        acc_map = {a.id: a.account_id for a in acc_rows}
        # 账号所属用户（管理员跨用户预览时，官方快照信号按商品实际 owner 隔离）
        acc_owner_map = {a.id: a.owner_id for a in acc_rows}

        # 指定账号预览：只保留所选账号的在售商品（不传=全部账号）
        allowed_pks: Optional[set] = None
        if account_ids:
            requested = {s.strip() for s in account_ids.split(",") if s.strip()}
            if requested:
                pk_by_account = {a.account_id: a.id for a in acc_rows}
                allowed_pks = {pk_by_account[aid] for aid in requested if aid in pk_by_account}

        # 预览前先同步所选账号商品：本地快照可能落后于闲鱼（增量同步只抓首页，
        # 商品较多时本地库可能不完整/状态被误标），完整同步会刷新全部在售状态
        if refresh:
            from common.services.item_service import ItemService

            sync_accounts = [
                a for a in acc_rows if allowed_pks is None or a.id in allowed_pks
            ]
            if sync_accounts:
                try:
                    item_svc = ItemService(session)
                    sync_result = await item_svc.fetch_all_items_from_accounts(
                        accounts=sync_accounts, page_size=20, max_pages=None
                    )
                    logger.info(
                        f"[权重算法预览] 预览前同步完成（{len(sync_accounts)} 个账号）: "
                        f"{sync_result.get('message')}"
                    )
                except Exception as exc:
                    logger.warning(f"[权重算法预览] 预览前同步商品失败（回退本地数据）: {exc}")

        item_stmt = select(XYCatalogItem)
        if scope_owner is not None:
            item_stmt = item_stmt.where(XYCatalogItem.owner_id == scope_owner)
        rows = list((await session.execute(item_stmt)).scalars().all())
        on_sale_rows = [
            r
            for r in rows
            if _normalize_item_status((r.metadata_json or {}).get("item_status")) == "on_sale"
        ]
        if allowed_pks is not None:
            on_sale_rows = [r for r in on_sale_rows if r.account_pk in allowed_pks]
        if not on_sale_rows:
            return ApiResponse(
                success=True,
                message="所选账号暂无在售商品，无法预览" if allowed_pks is not None else "暂无在售商品，无法预览",
                data={"algorithm": _to_dict(algo), "total": 0, "list": []},
            )

        by_account: Dict[int, list] = {}
        for r in on_sale_rows:
            by_account.setdefault(r.account_pk, []).append(r)

        details = []
        for acc_pk, items in by_account.items():
            account_id = acc_map.get(acc_pk)
            if account_id is None:
                continue
            owner_id = acc_owner_map.get(acc_pk) or current_user.id
            scored = await compute_delist_scores(
                owner_id, account_id, items, params=algo.params, session=session
            )
            details.extend(scored)
        details.sort(key=lambda d: d["weight"], reverse=True)

        return ApiResponse(
            success=True,
            message="预览成功",
            data={
                "algorithm": _to_dict(algo),
                "total": len(on_sale_rows),
                "list": [
                    {
                        "item_id": d["item"].item_id,
                        "title": d["item"].title or "",
                        "item_no": extract_prefix_number(d["item"].title),
                        "account_id": acc_map.get(d["item"].account_pk),
                        "weight": d["weight"],
                        "signals": d["signals"],
                        "parts": d["parts"],
                        "p_values": d.get("p_values") or {},
                        "clamped": d["clamped"],
                    }
                    for d in details
                ],
            },
        )

    # 加载全部素材（分页取完，预览要真实全量）
    mat_svc = ProductMaterialService(session)
    materials = []
    page = 1
    total = 0
    while True:
        result = await mat_svc.list_materials(user_id=current_user.id, page=page, page_size=1000)
        rows = result["list"]
        materials.extend(rows)
        total = int(result.get("total") or 0)
        if not rows or page * 1000 >= total:
            break
        page += 1

    if not materials:
        return ApiResponse(
            success=True,
            message="当前账号暂无素材，无法预览",
            data={"algorithm": _to_dict(algo), "total": 0, "list": []},
        )

    # 当前用户本地状态为「在售」的编号（执行时去重硬过滤会先排除这些；实际以规则账号刷新为准）
    from common.models.xy_catalog_item import XYCatalogItem
    from common.services.item_service import _normalize_item_status

    on_sale_nos = set()
    for title, meta in (
        await session.execute(
            select(XYCatalogItem.title, XYCatalogItem.metadata_json).where(
                XYCatalogItem.owner_id == current_user.id
            )
        )
    ).all():
        num = extract_prefix_number(title)
        if num is not None and _normalize_item_status((meta or {}).get("item_status")) == "on_sale":
            on_sale_nos.add(num)

    details = await compute_material_weight_details(
        current_user.id, materials, params=algo.params, session=session
    )
    return ApiResponse(
        success=True,
        message="预览成功",
        data={
            "algorithm": _to_dict(algo),
            "total": total,
            "list": [
                {
                    "material_id": d["material"].get("id"),
                    "title": d["material"].get("title") or "",
                    "item_no": extract_prefix_number(d["material"].get("title")),
                    "weight": d["weight"],
                    "signals": d["signals"],
                    "parts": d["parts"],
                    "p_values": d.get("p_values") or {},
                    "clamped": d["clamped"],
                    "on_sale_filtered": extract_prefix_number(d["material"].get("title"))
                    in on_sale_nos,
                }
                for d in details
            ],
        },
    )


@router.get("/{algorithm_id}/references", response_model=ApiResponse)
async def list_weight_algorithm_references(
    algorithm_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """查看引用该算法的规则列表（管理员跨用户视角，按算法类型返回发布/下架规则）"""
    algo = (
        await session.execute(
            select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
        )
    ).scalar_one_or_none()
    if algo is None:
        return ApiResponse(success=False, message="算法不存在")

    if algo.algorithm_type == "delist_weight":
        # 下架算法：返回引用它的定时下架规则
        rows = (
            await session.execute(
                select(OfflineSchedule)
                .where(OfflineSchedule.delist_algorithm_id == algorithm_id)
                .order_by(OfflineSchedule.id.desc())
            )
        ).scalars().all()
        return ApiResponse(
            success=True,
            message="查询成功",
            data={
                "list": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "user_id": r.user_id,
                        "max_count": r.max_count,
                        "enabled": bool(r.enabled),
                        "next_trigger_at": safe_isoformat(r.next_trigger_at),
                    }
                    for r in rows
                ],
            },
        )

    rows = (
        await session.execute(
            select(PublishSchedule)
            .where(PublishSchedule.weight_algorithm_id == algorithm_id)
            .order_by(PublishSchedule.id.desc())
        )
    ).scalars().all()
    return ApiResponse(
        success=True,
        message="查询成功",
        data={
            "list": [
                {
                    "id": r.id,
                    "name": r.name,
                    "user_id": r.user_id,
                    "publish_mode": r.publish_mode or "specified",
                    "random_count": r.random_count,
                    "enabled": bool(r.enabled),
                    "next_trigger_at": safe_isoformat(r.next_trigger_at),
                }
                for r in rows
            ],
        },
    )


@router.patch("/{algorithm_id}/toggle", response_model=ApiResponse)
async def toggle_weight_algorithm(
    algorithm_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """切换算法启用状态（停用后引用它的规则自动回退系统默认参数）"""
    algo = (
        await session.execute(
            select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
        )
    ).scalar_one_or_none()
    if algo is None:
        return ApiResponse(success=False, message="算法不存在")
    if algo.is_builtin:
        return ApiResponse(success=False, message="系统内置算法不可停用")
    algo.enabled = not algo.enabled
    await session.commit()
    return ApiResponse(
        success=True,
        message=f"算法已{'启用' if algo.enabled else '停用'}",
        data={"enabled": bool(algo.enabled)},
    )
