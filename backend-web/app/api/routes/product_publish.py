"""
商品发布 API 路由

功能：
1. 素材库管理（CRUD）
2. 单品发布（调用闲鱼卖家工作台接口）
3. 批量发布（后台任务异步执行，逐条调用闲鱼发布接口）
4. 发布日志查询（分页+过滤）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from app.services.product_publish_service import MaterialSpecificationError, ProductMaterialService
from app.services.account_service import AccountService
from app.services.platform_category_service import CategoryRecommendationError, PlatformCategoryService
from app.services.platform_category_defaults import DEFAULT_PLATFORM_CATEGORIES
from app.services.publish_batch_status_service import PublishBatchStatusService
from app.services.publish_execution_service import PublishExecutorService, PublishLogService
from common.models.user import User, UserRole
from common.schemas.common import ApiResponse
from common.utils.local_image_upload import ImageUploadError, save_uploaded_image
from common.utils.local_video_upload import VideoUploadError, save_uploaded_video
from app.core.paths import get_upload_path
from common.utils.time_utils import get_beijing_now, get_beijing_now_naive

def _is_admin(user: User) -> bool:
    """判断用户是否为管理员"""
    return user.role == UserRole.ADMIN


def _platform_category_fields(source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """平台分类字段，逐条传入的值（智能识别结果）优先，缺省用内置默认值。"""
    source = source or {}
    default = DEFAULT_PLATFORM_CATEGORIES[0]

    def pick(key: str, fallback: Any) -> Any:
        value = source.get(key)
        return value if value not in (None, "", []) else fallback

    return {
        "platform_category_id": pick("platform_category_id", default.get("cat_id") or ""),
        "platform_category_name": pick("platform_category_name", default["name"]),
        "platform_channel_category_id": pick("platform_channel_category_id", default["channel_cat_id"]),
        "platform_channel_category_name": pick("platform_channel_category_name", default["name"]),
        "platform_category_path": pick("platform_category_path", [
            {"id": default.get("cat_id") or default["channel_cat_id"], "name": default["name"]}
        ]),
    }

router = APIRouter(prefix="/product-publish", tags=["商品发布"])


# ==================== Pydantic 请求 / 响应模型 ====================

class PlatformAttributeRequest(BaseModel):
    """闲鱼平台属性标签，来源于抓包中的 itemLabelExtList。"""
    property_id: Optional[str] = Field(None, max_length=64)
    property_name: Optional[str] = Field(None, max_length=100)
    value_id: Optional[str] = Field(None, max_length=64)
    value_name: Optional[str] = Field(None, max_length=200)
    text: Optional[str] = Field(None, max_length=200)
    properties: Optional[str] = Field(None, max_length=500)


class PlatformCategoryPathItemRequest(BaseModel):
    """平台分类路径中的一级分类。"""
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)


class VideoMaterialRequest(BaseModel):
    """视频素材元数据，不保存抓包中的临时上传授权。"""
    url: str = Field(..., min_length=1, max_length=2000)
    path: Optional[str] = Field(None, max_length=1000)
    name: Optional[str] = Field(None, max_length=255)
    size: Optional[int] = Field(None, ge=0, le=200 * 1024 * 1024)
    file_id: Optional[str] = Field(None, max_length=128)
    width: Optional[int] = Field(None, ge=1, le=10000)
    height: Optional[int] = Field(None, ge=1, le=10000)
    duration_ms: Optional[int] = Field(None, ge=1, le=86400000)


class SpecificationValueRequest(BaseModel):
    """单个商品规格值。"""

    name: str = Field(..., min_length=1, max_length=100)
    image: Optional[str] = Field(None, max_length=2000)


class ProductSpecificationRequest(BaseModel):
    """商品规格类型及其可选值。"""

    name: str = Field(..., min_length=1, max_length=100)
    values: List[SpecificationValueRequest] = Field(default_factory=list, max_length=50)
    support_image: bool = False


class PublishSkuRowRequest(BaseModel):
    """规格组合对应的价格和库存。"""

    specs: Dict[str, str] = Field(default_factory=dict, max_length=4)
    price: float = Field(..., gt=0)
    stock: int = Field(0, ge=0, le=999999)

class MaterialCreateRequest(BaseModel):
    """创建素材请求"""
    title: str = Field(..., min_length=1, max_length=200, description="商品标题")
    description: str = Field(..., min_length=1, max_length=1500, description="商品描述")
    price: float = Field(..., gt=0, description="售价")
    original_price: Optional[float] = Field(None, description="原价（划线价）")
    category: Optional[str] = Field(None, max_length=100, description="商品分类")
    platform_category_id: Optional[str] = Field(None, max_length=64, description="平台末级分类ID（catId）")
    platform_category_name: Optional[str] = Field(None, max_length=100, description="平台末级分类名称（catName）")
    platform_channel_category_id: Optional[str] = Field(None, max_length=64, description="平台频道分类ID（channelCatId）")
    platform_channel_category_name: Optional[str] = Field(None, max_length=100, description="平台频道分类名称（channelCatName）")
    platform_leaf_id: Optional[str] = Field(None, max_length=64, description="平台叶子分类ID（leafId）")
    platform_tb_category_id: Optional[str] = Field(None, max_length=64, description="淘宝分类ID（tbCatId）")
    platform_category_path: List[PlatformCategoryPathItemRequest] = Field(default_factory=list)
    platform_attributes: List[PlatformAttributeRequest] = Field(default_factory=list, max_length=30)
    category_source: str = Field("manual", pattern="^(manual|recommendation)$")
    category_confidence: Optional[float] = Field(None, ge=0, le=1)
    images: List[str] = Field(..., min_length=1, max_length=9, description="图片URL列表（至少1张，最多9张）")
    videos: List[VideoMaterialRequest] = Field(default_factory=list, max_length=3)
    specifications: List[ProductSpecificationRequest] = Field(default_factory=list, max_length=2)
    sku_rows: List[PublishSkuRowRequest] = Field(default_factory=list, max_length=200)
    quantity: int = Field(1, ge=1, le=999999, description="发布数量")
    delivery_method: str = Field("express", description="发货方式：express/pickup")
    shipping_method: str = Field("free", pattern="^(free|distance|fixed|template|none)$")
    support_pickup: bool = False
    postage: float = Field(0, ge=0, description="邮费，0表示包邮")
    address: Optional[str] = Field(None, max_length=200, description="宝贝所在地")
    address_expected_text: Optional[str] = Field(None, max_length=200)
    brand: Optional[str] = Field(None, max_length=100, description="品牌")
    condition: str = Field("全新", description="成色")
    stock: int = Field(9999, ge=0, description="库存数量（鱼小铺账号可用）")
    remark: Optional[str] = Field(None, max_length=500, description="备注（内部使用）")


class MaterialUpdateRequest(BaseModel):
    """更新素材请求（所有字段均可选）"""
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=1500)
    price: Optional[float] = Field(None, gt=0)
    original_price: Optional[float] = None
    category: Optional[str] = Field(None, max_length=100)
    platform_category_id: Optional[str] = Field(None, max_length=64)
    platform_category_name: Optional[str] = Field(None, max_length=100)
    platform_channel_category_id: Optional[str] = Field(None, max_length=64)
    platform_channel_category_name: Optional[str] = Field(None, max_length=100)
    platform_leaf_id: Optional[str] = Field(None, max_length=64)
    platform_tb_category_id: Optional[str] = Field(None, max_length=64)
    platform_category_path: Optional[List[PlatformCategoryPathItemRequest]] = Field(None)
    platform_attributes: Optional[List[PlatformAttributeRequest]] = Field(None, max_length=30)
    category_source: Optional[str] = Field(None, pattern="^(manual|recommendation)$")
    category_confidence: Optional[float] = Field(None, ge=0, le=1)
    images: Optional[List[str]] = Field(None, min_length=1, max_length=9)
    videos: Optional[List[VideoMaterialRequest]] = Field(None, max_length=3)
    specifications: Optional[List[ProductSpecificationRequest]] = Field(None, max_length=2)
    sku_rows: Optional[List[PublishSkuRowRequest]] = Field(None, max_length=200)
    quantity: Optional[int] = Field(None, ge=1, le=999999)
    delivery_method: Optional[str] = Field(None, pattern="^(express|pickup)$")
    shipping_method: Optional[str] = Field(None, pattern="^(free|distance|fixed|template|none)$")
    support_pickup: Optional[bool] = None
    postage: Optional[float] = Field(None, ge=0)
    address: Optional[str] = Field(None, max_length=200)
    address_expected_text: Optional[str] = Field(None, max_length=200)
    brand: Optional[str] = Field(None, max_length=100)
    condition: Optional[str] = Field(None, max_length=20)
    stock: Optional[int] = Field(None, ge=0, description="库存数量")
    remark: Optional[str] = Field(None, max_length=500)


class PublishSingleRequest(BaseModel):
    """单品发布请求"""
    account_id: str = Field(..., description="闲鱼账号ID（cookie_id）")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1500)
    price: float = Field(..., gt=0)
    original_price: Optional[float] = None
    stock: int = Field(9999, ge=0, description="库存数量（鱼小铺账号可用）")
    category: Optional[str] = Field(None, description="商品分类")
    images: List[str] = Field(..., min_length=1, description="图片本地路径列表（至少1张）")
    platform_category_id: Optional[str] = Field(None, max_length=64)
    platform_category_name: Optional[str] = Field(None, max_length=100)
    platform_channel_category_id: Optional[str] = Field(None, max_length=64)
    platform_channel_category_name: Optional[str] = Field(None, max_length=100)
    platform_leaf_id: Optional[str] = Field(None, max_length=64)
    platform_tb_category_id: Optional[str] = Field(None, max_length=64)
    platform_category_path: List[PlatformCategoryPathItemRequest] = Field(default_factory=list)
    platform_attributes: List[PlatformAttributeRequest] = Field(default_factory=list, max_length=30)
    category_source: str = Field("manual", pattern="^(manual|recommendation)$")
    category_confidence: Optional[float] = Field(None, ge=0, le=1)
    videos: List[VideoMaterialRequest] = Field(default_factory=list, max_length=3)
    quantity: int = Field(1, ge=1, le=999999)
    stock: Optional[int] = Field(None, ge=0, le=999999)
    specifications: List[ProductSpecificationRequest] = Field(default_factory=list, max_length=2)
    sku_rows: List[PublishSkuRowRequest] = Field(default_factory=list, max_length=200)
    address: Optional[str] = None
    address_expected_text: Optional[str] = Field(None, max_length=200)
    delivery_method: str = Field("express", description="发货方式：express/pickup")
    shipping_method: str = Field("free", pattern="^(free|distance|fixed|template|none)$")
    support_pickup: bool = False
    postage: float = Field(0, ge=0, description="邮费，0表示包邮")
    brand: Optional[str] = Field(None, description="品牌")
    condition: str = Field("全新", description="成色")


class BatchPublishRequest(BaseModel):
    """批量发布请求"""
    account_ids: List[str] = Field(..., min_length=1, description="账号ID列表")
    material_ids: List[int] = Field(..., min_length=1, description="素材ID列表")


class CategoryRecommendRequest(BaseModel):
    """根据商品标题和描述请求平台分类推荐。"""

    title: str = Field("", max_length=200)
    description: str = Field("", max_length=1500)
    account_id: Optional[str] = Field(None, max_length=80, description="可选，指定用于请求的闲鱼账号")
    current_card_list: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="分类切换时沿用上一次推荐接口返回的完整属性卡",
    )
    selected_list: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="当前已选分类的属性标签列表",
    )
    cat_id: str = Field("", max_length=80, description="当前已选闲鱼末级分类ID")
    cat_name: str = Field("", max_length=200, description="当前已选分类名称")
    channel_cat_id: str = Field("", max_length=80, description="当前已选频道分类ID")


@router.post("/category/recommend", response_model=ApiResponse)
async def recommend_category(
    req: CategoryRecommendRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """按商品标题和描述调用闲鱼分类推荐接口，素材库自动轮换当前用户已启动账号。"""
    title = req.title.strip()
    description = req.description.strip()
    if not title and not description:
        return ApiResponse(success=False, message="请先填写商品标题或商品描述")

    account_service = AccountService(session)
    requested_account_id = (req.account_id or "").strip()
    if requested_account_id:
        owner_scope = None if _is_admin(current_user) else current_user.id
        account = await account_service.get_account_for_user(owner_scope, requested_account_id)
        if not account:
            return ApiResponse(success=False, message="指定的闲鱼账号不存在或无权使用")
        candidate_accounts = [account] if account.cookie else []
    else:
        # 素材库不指定账号，只能使用当前登录用户自己的已启动账号，管理员也不跨用户取账号。
        current_user_accounts = await account_service.list_accounts(current_user.id)
        started_accounts = [
            item
            for item in current_user_accounts
            if (item.status or "").strip().lower() == "active"
        ]
        if not started_accounts:
            return ApiResponse(success=False, message="当前用户没有已启动的闲鱼账号，请先启动账号")
        candidate_accounts = [item for item in started_accounts if item.cookie and item.cookie.strip()]

    if not candidate_accounts:
        message = (
            "指定的闲鱼账号缺少Cookie，请重新登录账号"
            if requested_account_id
            else "当前用户已启动的闲鱼账号均缺少Cookie，请重新登录账号"
        )
        return ApiResponse(success=False, message=message)

    category_service = PlatformCategoryService()
    last_error = "分类推荐失败，请稍后重试"
    for account_index, account in enumerate(candidate_accounts):
        try:
            data = await category_service.recommend(
                # 闲鱼接口要求两个字段都有值，单独填写描述时用描述作为标题，反之亦然。
                title=title or description[:200],
                description=description or title,
                cookie=account.cookie,
                account_id=account.account_id,
                owner_id=account.owner_id,
                current_card_list=req.current_card_list or None,
                selected_list=req.selected_list or None,
                cat_id=req.cat_id,
                cat_name=req.cat_name,
                channel_cat_id=req.channel_cat_id,
            )
            return ApiResponse(success=True, message="分类推荐成功", data=data)
        except CategoryRecommendationError as exc:
            last_error = str(exc)
            logger.warning(
                f"分类推荐账号不可用: user_id={current_user.id}, "
                f"account_id={account.account_id}, error={last_error}"
            )
        except Exception as exc:
            last_error = "分类推荐失败，请稍后重试"
            logger.error(
                f"分类推荐接口异常: user_id={current_user.id}, "
                f"account_id={account.account_id}, error={exc}"
            )

        if account_index < len(candidate_accounts) - 1:
            logger.info(
                f"分类推荐自动切换下一个已启动账号: user_id={current_user.id}, "
                f"failed_account_id={account.account_id}"
            )

    if requested_account_id:
        return ApiResponse(success=False, message=last_error)
    return ApiResponse(
        success=False,
        message=f"当前用户已启动的闲鱼账号均不可用：{last_error}",
    )


# ==================== 素材库接口 ====================

@router.post("/materials", response_model=ApiResponse)
async def create_material(
    req: MaterialCreateRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """创建商品素材"""
    svc = ProductMaterialService(session)
    try:
        material = await svc.create(current_user.id, req.model_dump())
    except MaterialSpecificationError as exc:
        return ApiResponse(success=False, message=str(exc))
    return ApiResponse(success=True, message="素材创建成功", data={"id": material.id})


@router.get("/materials", response_model=ApiResponse)
async def list_materials(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, description="每页条数"),
    title: str = Query(None, description="标题模糊搜索"),
    category: str = Query(None, description="分类筛选"),
    condition: str = Query(None, description="成色筛选"),
    platform_category_id: str = Query(None, description="平台分类ID筛选"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询素材列表（管理员可查看所有用户的素材）"""
    svc = ProductMaterialService(session)
    # 管理员查看全部，普通用户只看自己的
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_materials(
        query_user_id, page=page, page_size=page_size,
        title=title, category=category, condition=condition,
        platform_category_id=platform_category_id,
    )
    # 管理员场景：批量补充用户名
    if _is_admin(current_user) and data.get("list"):
        from sqlalchemy import select
        user_ids = list({m["user_id"] for m in data["list"]})
        stmt = select(User.id, User.username).where(User.id.in_(user_ids))
        rows = (await session.execute(stmt)).all()
        name_map = {r.id: r.username for r in rows}
        for m in data["list"]:
            m["username"] = name_map.get(m["user_id"], "未知用户")
    return ApiResponse(success=True, message="查询成功", data=data)


class BatchDeleteRequest(BaseModel):
    """批量删除素材请求"""
    ids: List[int] = Field(..., min_length=1, description="素材ID列表")


@router.post("/materials/batch-delete", response_model=ApiResponse)
async def batch_delete_materials(
    req: BatchDeleteRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """批量删除素材（管理员可删除任意素材）"""
    svc = ProductMaterialService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    count = await svc.batch_delete(req.ids, query_user_id)
    return ApiResponse(success=True, message=f"成功移出 {count} 条素材", data={"deleted_count": count})


@router.get("/materials/{material_id}", response_model=ApiResponse)
async def get_material(
    material_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """获取单条素材详情（管理员可访问任意素材）"""
    svc = ProductMaterialService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    material = await svc.get(material_id, query_user_id)
    if not material:
        return ApiResponse(success=False, message="素材不存在或无权访问")
    from app.services.product_publish_service import _material_to_dict
    return ApiResponse(success=True, message="查询成功", data=_material_to_dict(material))


@router.put("/materials/{material_id}", response_model=ApiResponse)
async def update_material(
    material_id: int,
    req: MaterialUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """更新素材信息（管理员可修改任意素材）"""
    svc = ProductMaterialService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    try:
        updated = await svc.update(
            material_id,
            query_user_id,
            # 只忽略请求中未出现的字段；显式传入的空数组、False 或 null 都要保存，
            # 否则编辑素材时清空规格/属性会被旧值覆盖。
            req.model_dump(exclude_unset=True),
        )
    except MaterialSpecificationError as exc:
        return ApiResponse(success=False, message=str(exc))
    if not updated:
        return ApiResponse(success=False, message="素材不存在或无权修改")
    return ApiResponse(success=True, message="素材更新成功")


@router.delete("/materials/{material_id}", response_model=ApiResponse)
async def delete_material(
    material_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """删除素材（管理员可删除任意素材）"""
    svc = ProductMaterialService(session)
    query_user_id = None if _is_admin(current_user) else current_user.id
    deleted = await svc.delete(material_id, query_user_id)
    if not deleted:
        return ApiResponse(success=False, message="素材不存在或无权删除")
    return ApiResponse(success=True, message="素材已移出素材库")


# ==================== 发布接口 ====================

@router.post("/publish/single", response_model=ApiResponse)
async def publish_single(
    req: PublishSingleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """单品发布（同步调用闲鱼卖家工作台接口并返回结果）。"""
    svc = PublishExecutorService(session)
    result = await svc.publish_single(
        user_id=current_user.id,
        account_id=req.account_id,
        item_data=req.model_dump(),
    )
    return ApiResponse(
        success=result.get("success", False),
        message=result.get("message", ""),
        data={
            "item_url": result.get("item_url"),
            "item_id": result.get("item_id"),
            "log_id": result.get("log_id"),
            "sync_status": result.get("sync_status"),
            "sync_message": result.get("sync_message"),
            "sync_total_count": result.get("sync_total_count"),
            "sync_saved_count": result.get("sync_saved_count"),
        },
    )


@router.post("/publish/batch", response_model=ApiResponse)
async def publish_batch(
    req: BatchPublishRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """批量发布（后台异步执行，立即返回 batch_id）
    
    前端通过 GET /publish/batch/{batch_id}/status 查询进度。
    后台会按账号循环，每个账号依次通过闲鱼接口发布所有素材。
    """
    mat_svc = ProductMaterialService(session)
    from app.services.product_publish_service import _material_to_dict
    materials = [_material_to_dict(m) for m in await mat_svc.list_by_ids(req.material_ids, current_user.id)]

    if not materials:
        return ApiResponse(success=False, message="没有找到有效的素材")

    batch_id = str(uuid.uuid4())
    await PublishBatchStatusService.init_batch(
        batch_id=batch_id,
        account_ids=req.account_ids,
        material_count=len(materials),
    )

    # 创建后台任务
    background_tasks.add_task(
        _run_batch_publish_background,
        user_id=current_user.id,
        account_ids=req.account_ids,
        materials=materials,
        batch_id=batch_id,
    )

    return ApiResponse(
        success=True,
        message=f"批量发布任务已提交，共 {len(req.account_ids)} 个账号 × {len(materials)} 件商品",
        data={
            "batch_id": batch_id,
            "total": len(req.account_ids) * len(materials),
        },
    )


@router.get("/publish/batch/{batch_id}/status", response_model=ApiResponse)
async def get_batch_status(
    batch_id: str,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """查询批量发布任务进度"""
    from sqlalchemy import select, func
    from common.models.publish_log import PublishLog

    unknown_sync_message = "批量任务同步状态缓存不存在，无法判断自动获取商品结果"

    stmt = select(
        PublishLog.status,
        func.count().label("cnt"),
    ).where(
        PublishLog.batch_id == batch_id,
        PublishLog.user_id == current_user.id,
    ).group_by(PublishLog.status)

    rows = (await session.execute(stmt)).all()
    counts = {r.status: r.cnt for r in rows}

    account_stmt = select(
        PublishLog.account_id,
        PublishLog.status,
        func.count().label("cnt"),
    ).where(
        PublishLog.batch_id == batch_id,
        PublishLog.user_id == current_user.id,
    ).group_by(PublishLog.account_id, PublishLog.status)
    account_rows = (await session.execute(account_stmt)).all()

    account_count_map: Dict[str, Dict[str, int]] = {}
    for row in account_rows:
        status_map = account_count_map.setdefault(row.account_id, {})
        status_map[row.status] = int(row.cnt)

    total = sum(counts.values())
    success = counts.get("success", 0)
    failed = counts.get("failed", 0)
    publishing = counts.get("publishing", 0)
    pending = counts.get("pending", 0)
    batch_snapshot = await PublishBatchStatusService.get_batch_snapshot(batch_id)

    if batch_snapshot is None:
        if total == 0:
            return ApiResponse(success=False, message="批量任务不存在或状态已失效")
        return ApiResponse(success=False, message="批量任务状态已失效，请到发布日志查看执行结果")

    account_statuses: List[Dict[str, Any]] = []
    if batch_snapshot:
        material_count = int(batch_snapshot.get("material_count") or 0)
        account_order = batch_snapshot.get("account_order") or []
        account_sync_map = batch_snapshot.get("accounts") or {}
        expected_total = material_count * len(account_order)
        if expected_total > total:
            total = expected_total
            pending = max(total - success - failed - publishing, 0)

        for account_id in account_order:
            status_map = account_count_map.get(account_id, {})
            account_total = material_count if material_count > 0 else sum(status_map.values())
            account_success = int(status_map.get("success", 0))
            account_failed = int(status_map.get("failed", 0))
            account_publishing = int(status_map.get("publishing", 0))
            account_pending = max(account_total - account_success - account_failed - account_publishing, 0)
            sync_info = account_sync_map.get(account_id, {})
            account_statuses.append(
                {
                    "account_id": account_id,
                    "total": account_total,
                    "success": account_success,
                    "failed": account_failed,
                    "publishing": account_publishing,
                    "pending": account_pending,
                    "sync_status": sync_info.get("sync_status", "pending"),
                    "sync_message": sync_info.get("sync_message", "等待该账号发布完成后自动获取商品"),
                    "sync_total_count": int(sync_info.get("sync_total_count") or 0),
                    "sync_saved_count": int(sync_info.get("sync_saved_count") or 0),
                }
            )

        extra_account_ids = [account_id for account_id in account_count_map.keys() if account_id not in set(account_order)]
        for account_id in extra_account_ids:
            status_map = account_count_map.get(account_id, {})
            account_total = sum(status_map.values())
            account_success = int(status_map.get("success", 0))
            account_failed = int(status_map.get("failed", 0))
            account_publishing = int(status_map.get("publishing", 0))
            account_pending = int(status_map.get("pending", 0))
            account_statuses.append(
                {
                    "account_id": account_id,
                    "total": account_total,
                    "success": account_success,
                    "failed": account_failed,
                    "publishing": account_publishing,
                    "pending": account_pending,
                    "sync_status": "unknown",
                    "sync_message": unknown_sync_message,
                    "sync_total_count": 0,
                    "sync_saved_count": 0,
                }
            )
    sync_finished = all(
        account_status.get("sync_status") in {"success", "failed", "skipped", "unknown"}
        for account_status in account_statuses
    ) if account_statuses else True

    return ApiResponse(
        success=True,
        message="查询成功",
        data={
            "batch_id": batch_id,
            "total": total,
            "success": success,
            "failed": failed,
            "publishing": publishing,
            "pending": pending,
            "finished": total > 0 and (publishing + pending) == 0 and sync_finished,
            "account_statuses": account_statuses,
        },
    )


# ==================== 发布日志接口 ====================

@router.get("/logs", response_model=ApiResponse)
async def list_publish_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20),
    account_id: Optional[str] = Query(None, description="按账号过滤"),
    status: Optional[str] = Query(None, description="按状态过滤：pending/publishing/success/failed"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询发布日志（管理员可查看所有用户的发布日志）"""
    svc = PublishLogService(session)
    # 管理员查看全部，普通用户只看自己的
    query_user_id = None if _is_admin(current_user) else current_user.id
    data = await svc.list_logs(
        user_id=query_user_id,
        page=page,
        page_size=page_size,
        account_id=account_id,
        status=status,
    )
    # 管理员场景：批量补充用户名
    if _is_admin(current_user) and data.get("list"):
        from sqlalchemy import select
        user_ids = list({log["user_id"] for log in data["list"]})
        stmt = select(User.id, User.username).where(User.id.in_(user_ids))
        rows = (await session.execute(stmt)).all()
        name_map = {r.id: r.username for r in rows}
        for log in data["list"]:
            log["username"] = name_map.get(log["user_id"], "未知用户")
    return ApiResponse(success=True, message="查询成功", data=data)


@router.delete("/logs/clear", response_model=ApiResponse)
@router.post("/logs/clear", response_model=ApiResponse)
async def clear_publish_logs(
    days: int | None = Query(default=None, ge=0, description="保留最近N天的日志；0或不传则清空全部"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """清空发布日志（可指定保留最近 N 天，不传或传 0 则清空全部）"""
    from datetime import timedelta

    from loguru import logger
    from sqlalchemy import delete

    from common.models.publish_log import PublishLog

    try:
        stmt = delete(PublishLog).where(PublishLog.user_id == current_user.id)

        if days and days > 0:
            cutoff = get_beijing_now() - timedelta(days=days)
            stmt = stmt.where(PublishLog.created_at < cutoff)
            scope_label = f"{days}天前的"
        else:
            scope_label = "全部"

        result = await session.execute(stmt)
        await session.commit()

        deleted_count = result.rowcount or 0
        logger.info(f"[发布日志] 用户 {current_user.id} 已清空 {deleted_count} 条{scope_label}日志")
        return ApiResponse(
            success=True,
            message=f"已清空 {deleted_count} 条{scope_label}发布日志",
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"[发布日志] 清空日志失败: {e}")
        return ApiResponse(success=False, message=f"清空发布日志失败: {str(e)}")


# ==================== 批量导入接口 ====================

class ScanDirectoryRequest(BaseModel):
    """扫描本地目录请求"""
    path: str = Field(..., min_length=1, description="本地素材目录路径")


class BatchImportMaterialItem(BaseModel):
    """批量导入单条素材"""
    code: str = Field(..., description="素材编号")
    folder_name: str = Field(..., description="文件夹名")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    images: List[str] = Field(default=[], description="本地图片路径列表")
    price: float = Field(..., gt=0)
    original_price: Optional[float] = Field(None, description="原价（划线价）")
    category: str = Field("虚拟商品", max_length=100)
    condition: str = Field("全新")
    brand: str = Field("", max_length=100)
    delivery_method: str = Field("express")
    postage: float = Field(0, ge=0)
    stock: int = Field(9999, ge=0, description="库存数量")


class BatchImportRequest(BaseModel):
    """批量导入请求"""
    materials: List[BatchImportMaterialItem] = Field(..., min_length=1, description="要导入的素材列表")


@router.post("/materials/scan-directory", response_model=ApiResponse)
async def scan_directory(
    req: ScanDirectoryRequest,
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """扫描本地目录，解析素材（txt元数据 + 图片文件）

    目录结构要求：每个子文件夹为一个素材，包含：
    - 一个 .txt 文件（第一行=标题，最后非空行=编号，中间=描述）
    - 若干 .jpg/.png 图片（按文件名排序）
    """
    import os
    import re
    from pathlib import Path

    from loguru import logger

    dir_path = Path(req.path.strip())
    if not dir_path.exists():
        return ApiResponse(success=False, message=f"目录不存在: {dir_path}")
    if not dir_path.is_dir():
        return ApiResponse(success=False, message=f"路径不是目录: {dir_path}")

    materials: List[Dict[str, Any]] = []
    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}

    try:
        subdirs = sorted(
            [d for d in dir_path.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )
    except PermissionError:
        return ApiResponse(success=False, message=f"没有权限读取目录: {dir_path}")
    except Exception as e:
        logger.error(f"扫描目录异常: {e}")
        return ApiResponse(success=False, message=f"扫描目录失败: {e}")

    for subdir in subdirs:
        try:
            # 查找 .txt 文件
            txt_files = sorted(subdir.glob("*.txt"))
            if not txt_files:
                logger.warning(f"跳过无txt文件的目录: {subdir.name}")
                continue

            txt_path = txt_files[0]
            try:
                txt_content = txt_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    txt_content = txt_path.read_text(encoding="gbk")
                except Exception:
                    logger.warning(f"无法读取txt文件编码: {txt_path}")
                    continue

            lines = [l.strip() for l in txt_content.split("\n")]
            # 过滤掉完全空的行
            non_empty = [l for l in lines if l]

            if len(non_empty) < 2:
                logger.warning(f"txt文件内容不足: {txt_path}, 行数={len(non_empty)}")
                continue

            # 第一行 = 标题
            raw_title = non_empty[0]
            # 去掉【xxx】前缀
            title = re.sub(r'^【[^】]*】\s*', '', raw_title).strip()

            # 最后非空行 = 编号
            code = non_empty[-1].strip()

            # 中间行 = 描述
            # 如果只有2行非空行，描述为空
            if len(non_empty) <= 2:
                description = title
            else:
                description = "\n".join(non_empty[1:-1]).strip()

            if not description:
                description = title

            # 查找图片
            images = sorted(
                [
                    str(p) for p in subdir.iterdir()
                    if p.suffix.lower() in IMAGE_EXTS
                ],
                key=lambda p: (
                    # 按数字排序：1.jpg, 2.jpg, ...
                    int(re.search(r'(\d+)', os.path.basename(p)).group(1))
                    if re.search(r'(\d+)', os.path.basename(p))
                    else os.path.basename(p)
                ),
            )

            # 从描述中提取分类
            category = "虚拟商品"
            if "虚拟商品" in txt_content:
                category = "虚拟商品"
            elif any(kw in txt_content for kw in ["数码", "手机", "电脑", "电子"]):
                category = "数码家电"
            elif any(kw in txt_content for kw in ["服饰", "鞋", "包", "衣服", "穿"]):
                category = "服饰鞋包"
            elif any(kw in txt_content for kw in ["家居", "日用", "家具", "收纳"]):
                category = "家居日用"
            elif any(kw in txt_content for kw in ["书", "音像", "DVD", "CD"]):
                category = "图书音像"
            elif any(kw in txt_content for kw in ["美妆", "护肤", "化妆", "个护"]):
                category = "美妆个护"
            elif any(kw in txt_content for kw in ["母婴", "宝宝", "孕"]):
                category = "母婴用品"
            elif any(kw in txt_content for kw in ["运动", "户外", "健身", "瑜伽"]):
                category = "运动户外"
            elif any(kw in txt_content for kw in ["食品", "生鲜", "零食", "饮料"]):
                category = "食品生鲜"
            elif any(kw in txt_content for kw in ["PPT", "模板", "简历", "教程", "素材", "资料", "网盘", "电子"]):
                category = "虚拟商品"

            materials.append({
                "code": code,
                "folder_name": subdir.name,
                "title": title,
                "description": description,
                "images": images,
                "image_count": len(images),
                "category": category,
                "price": 0,  # 前端统一设置
            })
        except Exception as e:
            logger.warning(f"解析目录异常 {subdir.name}: {e}")
            continue

    logger.info(f"[扫描目录] 目录={dir_path}, 发现素材={len(materials)}")
    return ApiResponse(
        success=True,
        message=f"扫描完成，发现 {len(materials)} 个素材",
        data={"materials": materials, "total": len(materials)},
    )


@router.post("/materials/batch-import", response_model=ApiResponse)
async def batch_import_materials(
    req: BatchImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """批量导入素材（从本地目录复制图片并创建素材记录）"""
    import os
    import shutil
    import uuid as uuid_mod

    from loguru import logger

    from app.core.paths import UPLOADS_PRODUCTS

    svc = ProductMaterialService(session)
    imported = 0
    failed = 0
    failed_items: List[Dict[str, str]] = []

    for material_data in req.materials:
        try:
            # 复制图片到上传目录
            saved_urls: List[str] = []
            for src_path in material_data.images:
                src = os.path.normpath(src_path)
                if not os.path.isfile(src):
                    logger.warning(f"图片不存在，跳过: {src}")
                    continue

                ext = os.path.splitext(src)[1].lower()
                if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}:
                    ext = '.jpg'

                # 生成唯一文件名
                unique_name = f"{uuid_mod.uuid4().hex}{ext}"
                dest_dir = str(UPLOADS_PRODUCTS)
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, unique_name)

                shutil.copy2(src, dest)
                saved_urls.append(f"/static/uploads/products/{unique_name}")

            if not saved_urls and material_data.images:
                logger.warning(f"素材 {material_data.code} 没有成功复制任何图片")

            # 补充可能被清洗的字段
            title = material_data.title.strip() or material_data.folder_name
            description = material_data.description.strip() or title

            create_data = {
                "title": title,
                "description": description,
                "price": float(material_data.price),
                "original_price": float(material_data.original_price) if material_data.original_price else None,
                "category": material_data.category or "虚拟商品",
                "images": [u for u in saved_urls if u],
                "delivery_method": material_data.delivery_method or "express",
                "postage": float(material_data.postage) if material_data.postage else 0,
                "address": None,
                "brand": material_data.brand.strip() if material_data.brand else None,
                "condition": material_data.condition or "全新",
                "stock": int(material_data.stock) if material_data.stock is not None else 9999,
                "remark": f"批量导入自: {material_data.folder_name}",
            }
            create_data.update(_platform_category_fields())

            await svc.create(current_user.id, create_data)
            imported += 1
            logger.info(f"[批量导入] 成功: {material_data.code} - {title}")
        except Exception as e:
            failed += 1
            failed_items.append({"code": material_data.code, "reason": str(e)})
            logger.error(f"[批量导入] 失败: {material_data.code} - {e}")

    return ApiResponse(
        success=True,
        message=f"导入完成：成功 {imported} 条，失败 {failed} 条",
        data={
            "imported": imported,
            "failed": failed,
            "failed_items": failed_items,
        },
    )


@router.post("/materials/batch-import-upload", response_model=ApiResponse)
async def batch_import_materials_upload(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """批量导入素材（客户端上传文件，支持远程访问）

    接收 multipart/form-data：
    - materials: JSON 字符串，素材元数据数组，每项含 image_count 字段
    - img_{i}_{j}: 第 i 个素材的第 j 张图片文件

    客户端先解析本地目录结构（txt + 图片），再通过本接口上传到服务器。
    与 /materials/batch-import 不同，本接口直接接收文件而非服务器本地路径。
    """
    import json as json_mod
    import os
    import uuid as uuid_mod

    from loguru import logger

    from app.core.paths import UPLOADS_PRODUCTS

    form = await request.form()

    # 解析 materials JSON
    materials_raw = form.get("materials")
    if not materials_raw or not isinstance(materials_raw, str):
        return ApiResponse(success=False, message="缺少 materials 字段")

    try:
        materials_list: List[Dict[str, Any]] = json_mod.loads(materials_raw)
    except json_mod.JSONDecodeError as e:
        return ApiResponse(success=False, message=f"materials JSON 解析失败: {e}")

    if not isinstance(materials_list, list) or len(materials_list) == 0:
        return ApiResponse(success=False, message="materials 必须是非空数组")

    svc = ProductMaterialService(session)
    imported = 0
    failed = 0
    failed_items: List[Dict[str, str]] = []
    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}

    for i, mat in enumerate(materials_list):
        try:
            # 收集该素材的图片文件
            saved_urls: List[str] = []
            j = 0
            max_images = int(mat.get("image_count", 20))
            while j < max_images:
                field_name = f"img_{i}_{j}"
                upload_file = form.get(field_name)
                if upload_file is None:
                    break
                if not hasattr(upload_file, "filename"):
                    break

                filename_parts = getattr(upload_file, "filename", None)
                if not filename_parts:
                    j += 1
                    continue

                ext = os.path.splitext(str(filename_parts))[1].lower()
                if ext not in IMAGE_EXTS:
                    ext = '.jpg'

                unique_name = f"{uuid_mod.uuid4().hex}{ext}"
                dest_dir = str(UPLOADS_PRODUCTS)
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, unique_name)

                content = await upload_file.read()
                with open(dest, "wb") as f:
                    f.write(content)
                saved_urls.append(f"/static/uploads/products/{unique_name}")
                j += 1

            # 构建创建数据
            title = str(mat.get("title", mat.get("folder_name", ""))).strip()
            description = str(mat.get("description", "")).strip() or title
            folder_name = str(mat.get("folder_name", ""))

            create_data = {
                "title": title,
                "description": description,
                "price": float(mat.get("price", 0)),
                "original_price": float(mat.get("original_price", 0)) if mat.get("original_price") else None,
                "category": str(mat.get("category", "虚拟商品")),
                "images": [u for u in saved_urls if u],
                "delivery_method": str(mat.get("delivery_method", "express")),
                "shipping_method": str(mat.get("shipping_method", "free")),
                "support_pickup": bool(mat.get("support_pickup", False)),
                "postage": float(mat.get("postage", 0)),
                "address": None,
                "brand": str(mat.get("brand", "")).strip() or None,
                "condition": str(mat.get("condition", "全新")),
                "stock": int(mat.get("stock", 9999)),
                "quantity": int(mat.get("quantity", 1)),
                "remark": f"批量导入自: {folder_name}" if folder_name else None,
            }
            create_data.update(_platform_category_fields(mat))

            await svc.create(current_user.id, create_data)
            imported += 1
            logger.info(f"[批量导入上传] 成功: {mat.get('code', '?')} - {title}")
        except Exception as e:
            failed += 1
            code = mat.get("code", f"unknown_{i}")
            failed_items.append({"code": code, "reason": str(e)})
            logger.error(f"[批量导入上传] 失败: {code} - {e}")

    return ApiResponse(
        success=True,
        message=f"导入完成：成功 {imported} 条，失败 {failed} 条",
        data={
            "imported": imported,
            "failed": failed,
            "failed_items": failed_items,
        },
    )


# ==================== 图片上传接口 ====================

@router.post("/upload/images", response_model=ApiResponse)
async def upload_product_images(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """上传商品图片（支持多张，最多9张，每张最大5MB）

    返回本地文件路径列表，这些路径将直接传给 Playwright 的 set_input_files。
    """
    upload_dir = get_upload_path("products")

    if len(files) > 9:
        return ApiResponse(success=False, message="最多上传9张图片")

    saved_paths: List[str] = []
    saved_urls: List[str] = []

    for file in files:
        try:
            filepath, filename, _ = await save_uploaded_image(
                file,
                upload_dir,
            )
        except ImageUploadError as exc:
            # 在消息里带上具体哪张图片出错，方便前端展示
            return ApiResponse(
                success=False,
                message=f"文件 {file.filename}: {exc.message}",
            )

        saved_paths.append(str(filepath))                          # 绝对路径，用于 Playwright
        saved_urls.append(f"/static/uploads/products/{filename}")  # URL，用于前端预览

    return ApiResponse(
        success=True,
        message=f"成功上传 {len(saved_paths)} 张图片",
        data={"paths": saved_paths, "urls": saved_urls},
    )


@router.post("/upload/videos", response_model=ApiResponse)
async def upload_product_videos(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """上传商品视频（最多3个，每个最大100MB）。"""
    del current_user
    if len(files) > 3:
        return ApiResponse(success=False, message="最多上传3个视频")

    upload_dir = get_upload_path("products")
    videos: List[dict] = []
    for file in files:
        try:
            filepath, filename, size = await save_uploaded_video(file, upload_dir)
        except VideoUploadError as exc:
            return ApiResponse(success=False, message=f"文件 {file.filename}: {exc.message}")
        videos.append({
            "path": str(filepath),
            "url": f"/static/uploads/products/{filename}",
            "name": file.filename or filename,
            "size": size,
        })

    return ApiResponse(
        success=True,
        message=f"成功上传 {len(videos)} 个视频",
        data={
            "videos": videos,
            "paths": [item["path"] for item in videos],
            "urls": [item["url"] for item in videos],
        },
    )


# ==================== 后台任务函数 ====================

async def _run_batch_publish_background(
    user_id: int,
    account_ids: List[str],
    materials: List[dict],
    batch_id: str,
    schedule_log_id: int = None,
) -> None:
    """后台异步执行批量发布任务（可选关联定时发布的执行记录）"""
    from common.db.session import async_session_maker
    from loguru import logger
    import traceback

    async with async_session_maker() as session:
        svc = PublishExecutorService(session)
        try:
            # 直接将 batch_id 传给 service，确保日志与路由返回值一致
            result = await svc.batch_publish(
                user_id=user_id,
                account_ids=account_ids,
                materials=materials,
                batch_id=batch_id,
            )
            # 若关联了定时发布执行记录，同步更新结果
            if schedule_log_id:
                await _update_schedule_log_on_complete(
                    schedule_log_id,
                    success_count=result.get("success_count", 0),
                    failed_count=result.get("failed_count", 0),
                    is_error=False,
                )
        except Exception as e:
            logger.error(f"批量发布后台任务异常: {e}\n{traceback.format_exc()}")
            await PublishBatchStatusService.clear_batch(batch_id)
            if schedule_log_id:
                await _update_schedule_log_on_complete(
                    schedule_log_id,
                    success_count=0,
                    failed_count=0,
                    is_error=True,
                    error_message=str(e)[:800],
                )


async def _update_schedule_log_on_complete(
    schedule_log_id: int,
    success_count: int = 0,
    failed_count: int = 0,
    is_error: bool = False,
    error_message: str = None,
) -> None:
    """更新定时发布执行记录为完成/失败状态"""
    from common.db.session import async_session_maker
    from common.models.publish_schedule_log import PublishScheduleLog
    from sqlalchemy import select
    from loguru import logger

    try:
        async with async_session_maker() as session:
            stmt = select(PublishScheduleLog).where(PublishScheduleLog.id == schedule_log_id)
            log_entry = (await session.execute(stmt)).scalar_one_or_none()
            if log_entry:
                log_entry.status = "failed" if is_error else "completed"
                log_entry.success_count = success_count
                log_entry.failed_count = failed_count
                if error_message:
                    log_entry.error_message = error_message
                await session.commit()
                logger.info(
                    f"[定时发布] 执行记录 #{schedule_log_id} 已更新: "
                    f"status={log_entry.status}, success={success_count}, failed={failed_count}"
                )
    except Exception as e:
        logger.error(f"[定时发布] 更新执行记录 #{schedule_log_id} 失败: {e}")
