"""
数据分析路由模块

提供卖家数据罗盘各模块查询接口，支持多账号、多时间范围查询：
- 数据总览 / 流量分布 / 商品概览 / 商品列表 / 单品指标
- 复购概览 / 退款分析 / 粉丝概况 / 客服概览 / 流量转化漏斗
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.services.data_analysis_service import (
    fetch_browse_summary,
    fetch_cs_overview_summary,
    fetch_fans_summary,
    fetch_flow_detail,
    fetch_item_indicators,
    fetch_item_list,
    fetch_item_summary,
    fetch_refund_summary,
    fetch_repurchase_summary,
    fetch_seller_summary,
)
from common.models import User
from common.models.xy_account import XYAccount
from common.schemas.common import ApiResponse
from common.utils.auth_scope import resolve_owner_scope

router = APIRouter(prefix="/data-analysis", tags=["数据分析"])

# 合法时间范围类型
VALID_DATE_TYPES = ["recent1d", "recent7d", "recent30d", "customDate"]


class BaseDataRequest(BaseModel):
    """数据罗盘通用请求基类"""
    account_id: int = Field(..., description="账号ID")
    date_type: str = Field("recent7d", description="时间范围类型: recent1d/recent7d/recent30d/customDate")
    date_range: Optional[str] = Field("", description="自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）")


class ItemListRequest(BaseDataRequest):
    """商品列表请求"""
    page_num: int = Field(1, ge=1, description="页码（从1开始）")
    page_size: int = Field(20, ge=1, le=50, description="每页条数")


class ItemIndicatorsRequest(BaseDataRequest):
    """单品指标请求"""
    item_id: str = Field("", description="闲鱼商品ID（为空时仅返回指标字段定义表）")


async def _get_owned_account(
    account_id: int,
    current_user: User,
    db: AsyncSession,
) -> Optional[XYAccount]:
    """查询账号并校验归属权限，返回 None 表示不存在或无权限"""
    owner_id, is_admin = resolve_owner_scope(current_user)
    query = select(XYAccount).where(XYAccount.id == account_id)
    if not is_admin:
        query = query.where(XYAccount.owner_id == owner_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


def _validate_date_params(date_type: str, date_range: Optional[str]) -> Optional[str]:
    """校验时间范围参数，返回错误消息或 None"""
    if date_type not in VALID_DATE_TYPES:
        return f"无效的时间范围类型，支持: {', '.join(VALID_DATE_TYPES)}"
    if date_type == "customDate":
        if not date_range:
            return "自定义日期范围不能为空，格式: yyyyMMdd|yyyyMMdd"
        parts = (date_range or "").split("|")
        if len(parts) != 2 or len(parts[0]) != 8 or len(parts[1]) != 8:
            return "日期范围格式错误，正确格式: yyyyMMdd|yyyyMMdd"
    return None


async def _handle_request(
    request: BaseDataRequest,
    fetch_fn: Callable[..., Awaitable[Dict[str, Any]]],
    current_user: User,
    db: AsyncSession,
) -> ApiResponse:
    """数据罗盘请求公共处理：账号校验 → 参数校验 → 调用 → 包装响应"""
    account = await _get_owned_account(request.account_id, current_user, db)
    if not account:
        return ApiResponse(success=False, message="账号不存在或无权限访问", data=None)
    if not account.cookie:
        return ApiResponse(success=False, message="账号Cookie为空，请先登录", data=None)

    err = _validate_date_params(request.date_type, request.date_range)
    if err:
        return ApiResponse(success=False, message=err, data=None)

    api_result = await fetch_fn(
        cookies_str=account.cookie,
        date_type=request.date_type,
        date_range=request.date_range or "",
    )
    if api_result.get("success"):
        return ApiResponse(success=True, message="获取成功", data=api_result.get("data"))
    return ApiResponse(success=False, message=api_result.get("message", "获取数据失败"), data=None)


@router.post("/seller-summary")
async def get_seller_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取卖家数据概览

    返回 36 个核心指标（成交/流量/复购/商品运营/同行竞争力）+ 逐日趋势数据。
    """
    return await _handle_request(request, fetch_seller_summary, current_user, db)


@router.post("/browse-summary")
async def get_browse_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取流量分布数据

    返回来源分布、商品分布、时间分布、地域分布数据。
    """
    return await _handle_request(request, fetch_browse_summary, current_user, db)


@router.post("/item-summary")
async def get_item_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取商品维度概览

    返回 14 个商品运营指标（在架/上新/收藏/成交商品数等）。
    """
    return await _handle_request(request, fetch_item_summary, current_user, db)


@router.post("/repurchase-summary")
async def get_repurchase_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取复购概览

    返回复购订单数、复购买家数、复购率等 10 个指标。
    """
    return await _handle_request(request, fetch_repurchase_summary, current_user, db)


@router.post("/refund-summary")
async def get_refund_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取退款分析

    返回发货前/后、仅退款/退货退款、平台介入、卖家责任率等 34 个指标。
    """
    return await _handle_request(request, fetch_refund_summary, current_user, db)


@router.post("/fans-summary")
async def get_fans_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取粉丝概况

    返回粉丝总数、新增粉丝、粉丝下单占比 + 逐日趋势。
    """
    return await _handle_request(request, fetch_fans_summary, current_user, db)


@router.post("/cs-overview-summary")
async def get_cs_overview_summary(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取客服概览

    返回响应时长、3分钟回复率、满意度、客服成交额等 14 个指标 + 逐日趋势。
    """
    return await _handle_request(request, fetch_cs_overview_summary, current_user, db)


@router.post("/item-list")
async def get_item_list(
    request: ItemListRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取商品列表（分页）

    返回商品曝光/浏览/咨询/成交/退款等运营数据。
    """
    account = await _get_owned_account(request.account_id, current_user, db)
    if not account:
        return ApiResponse(success=False, message="账号不存在或无权限访问", data=None)
    if not account.cookie:
        return ApiResponse(success=False, message="账号Cookie为空，请先登录", data=None)

    err = _validate_date_params(request.date_type, request.date_range)
    if err:
        return ApiResponse(success=False, message=err, data=None)

    api_result = await fetch_item_list(
        cookies_str=account.cookie,
        date_type=request.date_type,
        date_range=request.date_range or "",
        page_num=request.page_num,
        page_size=request.page_size,
        seller_id=account.account_id,
    )
    if api_result.get("success"):
        return ApiResponse(success=True, message="获取成功", data=api_result.get("data"))
    return ApiResponse(success=False, message=api_result.get("message", "获取数据失败"), data=None)


@router.post("/item-indicators")
async def get_item_indicators(
    request: ItemIndicatorsRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取单品指标

    返回流量指标（5 项）、交易指标（4 项）、综合指标（6 项）。
    """
    if request.item_id and not request.item_id.isdigit():
        return ApiResponse(success=False, message="商品ID无效", data=None)

    account = await _get_owned_account(request.account_id, current_user, db)
    if not account:
        return ApiResponse(success=False, message="账号不存在或无权限访问", data=None)
    if not account.cookie:
        return ApiResponse(success=False, message="账号Cookie为空，请先登录", data=None)

    err = _validate_date_params(request.date_type, request.date_range)
    if err:
        return ApiResponse(success=False, message=err, data=None)

    api_result = await fetch_item_indicators(
        cookies_str=account.cookie,
        item_id=request.item_id,
        date_type=request.date_type,
        date_range=request.date_range or "",
    )
    if api_result.get("success"):
        return ApiResponse(success=True, message="获取成功", data=api_result.get("data"))
    return ApiResponse(success=False, message=api_result.get("message", "获取数据失败"), data=None)


@router.post("/flow-detail")
async def get_flow_detail(
    request: BaseDataRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """
    获取流量转化漏斗

    返回曝光→浏览→咨询→支付各环节 UV 与转化率。
    """
    return await _handle_request(request, fetch_flow_detail, current_user, db)
