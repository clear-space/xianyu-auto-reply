"""
上架权重算法管理路由（管理员）

功能：
1. 权重算法 CRUD（管理员集中定义调参规则）
2. 引用计数与删除保护（被定时发布规则引用的算法禁止删除）
3. 定时发布规则表单通过 /product-publish/schedules/weight-algorithms 读取启用中的算法
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models.publish_schedule import PublishSchedule
from common.models.user import User
from common.models.weight_algorithm import WeightAlgorithm
from common.schemas.common import ApiResponse
from common.services.material_scoring import DEFAULT_WEIGHT_PARAMS, normalize_weight_params
from common.utils.time_utils import safe_isoformat

router = APIRouter(tags=["上架权重算法"])


def _to_dict(algo: WeightAlgorithm, ref_count: int = 0) -> dict:
    return {
        "id": algo.id,
        "name": algo.name,
        "algorithm_type": algo.algorithm_type,
        "description": algo.description,
        "params": normalize_weight_params(algo.params),
        "enabled": bool(algo.enabled),
        "is_builtin": bool(algo.is_builtin),
        "ref_count": ref_count,
        "created_at": safe_isoformat(algo.created_at),
        "updated_at": safe_isoformat(algo.updated_at),
    }


class WeightAlgorithmCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="算法名称")
    algorithm_type: str = Field("heat_weight", max_length=32, description="算法类型：heat_weight-热度加权")
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
    return ApiResponse(
        success=True,
        message="查询成功",
        data={
            "list": [_to_dict(a, int(ref_map.get(a.id, 0))) for a in rows],
            "default_params": DEFAULT_WEIGHT_PARAMS,
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

    algo = WeightAlgorithm(
        name=payload.name.strip(),
        algorithm_type=payload.algorithm_type or "heat_weight",
        description=payload.description,
        params=normalize_weight_params(payload.params),
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
        # 内置算法：仅允许调整「硬排已售出」与「选料方式」，其余字段与参数保持只读（静默忽略）
        if payload.params is not None:
            normalized = normalize_weight_params(payload.params)
            merged = dict(algo.params or {})
            merged["exclude_sold"] = bool(normalized.get("exclude_sold", False))
            merged["sample_mode"] = normalized.get("sample_mode", "weighted")
            algo.params = merged
            await session.commit()
            await session.refresh(algo)
        return ApiResponse(
            success=True,
            message="内置算法已更新（仅「硬排已售出」与「选料方式」生效，其余参数只读）",
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
        algo.algorithm_type = payload.algorithm_type
    if payload.description is not None:
        algo.description = payload.description
    if payload.params is not None:
        algo.params = normalize_weight_params(payload.params)
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
    """删除权重算法（被定时发布规则引用时禁止删除）"""
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
    if ref_count > 0:
        return ApiResponse(
            success=False,
            message=f"该算法被 {ref_count} 条定时发布规则引用，无法删除（可先停用）",
        )

    await session.delete(algo)
    await session.commit()
    return ApiResponse(success=True, message="算法已删除")


@router.get("/{algorithm_id}/preview", response_model=ApiResponse)
async def preview_weight_algorithm(
    algorithm_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> dict:
    """预览算法效果：对当前用户全部素材计算权重，附信号明细与逐项分值"""
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
    """查看引用该算法的定时发布规则列表（管理员跨用户视角）"""
    algo = (
        await session.execute(
            select(WeightAlgorithm).where(WeightAlgorithm.id == algorithm_id)
        )
    ).scalar_one_or_none()
    if algo is None:
        return ApiResponse(success=False, message="算法不存在")

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
