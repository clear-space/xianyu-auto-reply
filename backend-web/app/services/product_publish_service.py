"""
商品发布业务逻辑服务

功能：
1. 素材库 CRUD（创建/查询/更新/删除商品模板）
2. 提供素材字典转换工具，供发布执行链路复用
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.product_material import (
    MATERIAL_RISK_DISABLED,
    ProductMaterial,
)
from app.services.xianyu_item_snapshot import as_bool


# ==================== 素材库服务 ====================

from common.utils.time_utils import safe_isoformat


class MaterialSpecificationError(ValueError):
    """商品素材规格不符合保存规则。"""


def _normalize_specifications(value: Any) -> list[dict]:
    """规范化规格 JSON，确保规格值名称和图片字段完整保存。"""
    if not isinstance(value, list):
        return []
    normalized: list[dict] = []
    for specification in value[:2]:
        if not isinstance(specification, dict):
            continue
        name = str(specification.get("name") or "").strip()
        if not name:
            continue
        values: list[dict] = []
        seen_values: set[str] = set()
        for item in specification.get("values") or []:
            if not isinstance(item, dict):
                continue
            value_name = str(item.get("name") or "").strip()
            if not value_name:
                continue
            if value_name in seen_values:
                raise MaterialSpecificationError(f"规格“{name}”存在重复规格值：{value_name}")
            seen_values.add(value_name)
            values.append({"name": value_name, "image": item.get("image") or None})
        normalized.append({
            "name": name,
            "support_image": bool(specification.get("support_image")),
            "values": values,
        })
    return normalized


def _normalize_sku_rows(value: Any) -> list[dict]:
    """规范化 SKU JSON，保留每个规格组合的价格和库存。"""
    if not isinstance(value, list):
        return []
    normalized: list[dict] = []
    for row in value[:200]:
        if not isinstance(row, dict):
            continue
        specs = row.get("specs") if isinstance(row.get("specs"), dict) else {}
        try:
            price = float(row.get("price"))
            stock = int(row.get("stock"))
        except (TypeError, ValueError):
            continue
        if price <= 0 or stock < 0:
            continue
        normalized.append({
            "specs": {str(key): str(item) for key, item in specs.items()},
            "price": price,
            "stock": stock,
        })
    return normalized


def _normalize_material_json(data: dict) -> dict:
    """统一处理素材中嵌套 JSON，避免 Pydantic/ORM 转换时丢字段。"""
    normalized = dict(data)
    normalized["specifications"] = _normalize_specifications(data.get("specifications"))
    normalized["sku_rows"] = _normalize_sku_rows(data.get("sku_rows"))
    normalized["platform_category_path"] = data.get("platform_category_path") or []
    normalized["platform_attributes"] = data.get("platform_attributes") or []
    normalized["videos"] = data.get("videos") or []
    normalized["images"] = data.get("images") or []
    return normalized


def _normalize_versions(value: Any) -> list[dict]:
    """规范化素材版本 JSON：每项 {version, title, description, images}，按版本号升序去重。"""
    if not isinstance(value, list):
        return []
    seen: dict[int, dict] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            version = int(item.get("version"))
        except (TypeError, ValueError):
            continue
        if version <= 0:
            continue
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        images = item.get("images") if isinstance(item.get("images"), list) else []
        if not title:
            continue
        seen[version] = {
            "version": version,
            "title": title[:200],
            "description": description or title,
            "images": [str(u) for u in images if str(u).strip()][:9],
        }
    return [seen[key] for key in sorted(seen)]


class ProductMaterialService:
    """商品素材库 CRUD 服务"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: int, data: dict) -> ProductMaterial:
        """创建素材"""
        data = _normalize_material_json(data)
        shipping_method = str(data.get("shipping_method") or "free")
        material = ProductMaterial(
            user_id=user_id,
            title=data["title"],
            description=data["description"],
            price=float(data["price"]),
            original_price=float(data["original_price"]) if data.get("original_price") else None,
            category=data.get("category"),
            platform_category_id=data.get("platform_category_id"),
            platform_category_name=data.get("platform_category_name"),
            platform_channel_category_id=data.get("platform_channel_category_id"),
            platform_channel_category_name=data.get("platform_channel_category_name"),
            platform_leaf_id=data.get("platform_leaf_id"),
            platform_tb_category_id=data.get("platform_tb_category_id"),
            platform_category_path=data.get("platform_category_path") or [],
            platform_attributes=data.get("platform_attributes") or [],
            category_source=data.get("category_source") or "manual",
            category_confidence=data.get("category_confidence"),
            images=data["images"],
            videos=data.get("videos") or [],
            specifications=data["specifications"],
            sku_rows=data["sku_rows"],
            quantity=int(data.get("quantity") or 1),
            # delivery_method 仅为兼容展示字段，实际发布由 shipping_method 决定。
            delivery_method="pickup" if shipping_method == "none" else "express",
            shipping_method=shipping_method,
            # 兼容 API/历史调用可能传入的 "true"/"false" 字符串，避免 bool("false") 为 True。
            support_pickup=as_bool(data.get("support_pickup", False)),
            postage=float(data.get("postage", 0)),
            address=data.get("address"),
            address_expected_text=data.get("address_expected_text"),
            brand=data.get("brand"),
            condition=data.get("condition", "全新"),
            stock=int(data.get("stock", 9999)),
            remark=data.get("remark"),
            risk=int(data.get("risk", 0)),
            product_code=data.get("product_code"),
            versions=_normalize_versions(data.get("versions")),
            default_version=int(data["default_version"]) if data.get("default_version") is not None else None,
        )
        self.session.add(material)
        await self.session.commit()
        await self.session.refresh(material)
        return material

    async def import_material(self, user_id: int, data: dict) -> ProductMaterial:
        """导入素材：同一商品编号保留所有版本（按版本号覆盖合并），默认版本 = 最大版本号。

        - 带 product_code + versions：主行 title/description/images 镜像默认版本内容；
        - 无版本信息（老格式/手工路径）：按普通创建处理，行为与旧版一致。
        """
        data = _normalize_material_json(data)
        product_code = str(data.get("product_code") or "").strip() or None
        versions = _normalize_versions(data.get("versions"))
        if not product_code or not versions:
            data.pop("product_code", None)
            data.pop("versions", None)
            data.pop("default_version", None)
            return await self.create(user_id, data)

        default_version = max(v["version"] for v in versions)
        data["product_code"] = product_code
        data["default_version"] = default_version
        data["versions"] = versions

        stmt = select(ProductMaterial).where(
            ProductMaterial.user_id == user_id,
            ProductMaterial.product_code == product_code,
            ProductMaterial.is_deleted.is_(False),
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()

        if existing:
            # 合并版本：本次导入的版本覆盖同名版本号，库中其他版本保留
            merged = {v["version"]: v for v in _normalize_versions(existing.versions)}
            for version_item in versions:
                merged[version_item["version"]] = version_item
            merged_versions = [merged[key] for key in sorted(merged)]
            data["versions"] = merged_versions
            data["default_version"] = max(merged)
            default_item = merged[data["default_version"]]
            data["title"] = default_item["title"]
            data["description"] = default_item["description"]
            data["images"] = default_item["images"]
            return await self.update(existing.id, user_id, data)

        default_item = next(v for v in versions if v["version"] == default_version)
        data["title"] = default_item["title"]
        data["description"] = default_item["description"]
        data["images"] = default_item["images"]
        return await self.create(user_id, data)

    async def list_materials(
        self, user_id: int = None, page: int = 1, page_size: int = 20,
        title: str = None, category: str = None, condition: str = None,
        platform_category_id: str = None, keyword: str = None,
        exclude_risk_disabled: bool = False,
    ) -> Dict[str, Any]:
        """分页查询素材列表

        Args:
            user_id: 用户ID，为None时查询全部（管理员场景）
            title: 标题模糊搜索
            category: 分类筛选
            condition: 成色筛选
            keyword: 关键词搜索（匹配标题或描述）
            exclude_risk_disabled: 是否排除风险=禁用(2)的素材（发布类场景用，素材库管理页保留显示）
        """
        page = max(page, 1)
        page_size = page_size if page_size in (10, 20, 50, 100, 500, 1000) else 20

        base_cond = [ProductMaterial.is_deleted.is_(False)]
        if exclude_risk_disabled:
            base_cond.append(ProductMaterial.risk != MATERIAL_RISK_DISABLED)
        if user_id is not None:
            base_cond.append(ProductMaterial.user_id == user_id)
        if title:
            base_cond.append(ProductMaterial.title.ilike(f"%{title}%"))
        if keyword:
            base_cond.append(or_(
                ProductMaterial.title.ilike(f"%{keyword}%"),
                ProductMaterial.description.ilike(f"%{keyword}%"),
            ))
        if category:
            base_cond.append(ProductMaterial.category == category)
        if condition:
            base_cond.append(ProductMaterial.condition == condition)
        if platform_category_id:
            base_cond.append(ProductMaterial.platform_category_id == platform_category_id)

        count_stmt = (
            select(func.count())
            .select_from(ProductMaterial)
            .where(*base_cond)
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(ProductMaterial)
            .where(*base_cond)
            .order_by(desc(ProductMaterial.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        return {
            "list": [_material_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    async def list_material_ids(
        self, user_id: int = None, title: str = None, category: str = None,
        condition: str = None, platform_category_id: str = None, keyword: str = None,
    ) -> List[int]:
        """查询素材ID列表（无分页，供前端"全选所有素材"使用）

        筛选条件与 list_materials 保持一致；风险=禁用(2)的素材始终排除
        （全选、scope=all 素材池、内部计数等发布类场景不可包含禁用素材）。
        """
        base_cond = [ProductMaterial.is_deleted.is_(False), ProductMaterial.risk != MATERIAL_RISK_DISABLED]
        if user_id is not None:
            base_cond.append(ProductMaterial.user_id == user_id)
        if title:
            base_cond.append(ProductMaterial.title.ilike(f"%{title}%"))
        if keyword:
            base_cond.append(or_(
                ProductMaterial.title.ilike(f"%{keyword}%"),
                ProductMaterial.description.ilike(f"%{keyword}%"),
            ))
        if category:
            base_cond.append(ProductMaterial.category == category)
        if condition:
            base_cond.append(ProductMaterial.condition == condition)
        if platform_category_id:
            base_cond.append(ProductMaterial.platform_category_id == platform_category_id)

        stmt = (
            select(ProductMaterial.id)
            .where(*base_cond)
            .order_by(desc(ProductMaterial.created_at))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, material_id: int, user_id: int = None) -> Optional[ProductMaterial]:
        """查询单条素材
        
        Args:
            material_id: 素材ID
            user_id: 用户ID，为None时不限用户（管理员场景）
        """
        conds = [ProductMaterial.id == material_id, ProductMaterial.is_deleted.is_(False)]
        if user_id is not None:
            conds.append(ProductMaterial.user_id == user_id)
        stmt = select(ProductMaterial).where(*conds)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_ids(self, material_ids: List[int], user_id: int) -> List[ProductMaterial]:
        """按ID查询素材（发布链路的硬过滤安全网：软删除与禁用(2)素材一律不返回）"""
        if not material_ids:
            return []
        unique_ids = list(dict.fromkeys(material_ids))
        material_map = {}
        # 分批查询：素材过多时避免 IN 子句超出 MySQL max_allowed_packet
        for i in range(0, len(unique_ids), 500):
            batch = unique_ids[i:i + 500]
            stmt = select(ProductMaterial).where(
                ProductMaterial.user_id == user_id,
                ProductMaterial.id.in_(batch),
                ProductMaterial.is_deleted.is_(False),
                ProductMaterial.risk != MATERIAL_RISK_DISABLED,
            )
            rows = (await self.session.execute(stmt)).scalars().all()
            for row in rows:
                material_map[row.id] = row
        return [material_map[mid] for mid in material_ids if mid in material_map]

    async def list_disabled_ids(self, material_ids: List[int], user_id: int) -> List[int]:
        """查询素材ID列表中处于禁用(2)状态的ID（供调用方给出明确报错提示）"""
        if not material_ids:
            return []
        unique_ids = list(dict.fromkeys(material_ids))
        disabled_map: dict[int, bool] = {}
        for i in range(0, len(unique_ids), 500):
            batch = unique_ids[i:i + 500]
            stmt = select(ProductMaterial.id, ProductMaterial.risk).where(
                ProductMaterial.user_id == user_id,
                ProductMaterial.id.in_(batch),
                ProductMaterial.is_deleted.is_(False),
            )
            for row in (await self.session.execute(stmt)).all():
                if row[1] == MATERIAL_RISK_DISABLED:
                    disabled_map[row[0]] = True
        return [mid for mid in material_ids if mid in disabled_map]

    async def sanitize_schedule_material_ids(
        self, material_ids: List[int], user_id: int
    ) -> tuple[List[int], List[int], List[int]]:
        """净化定时规则素材ID，返回 (有效ID列表, 缺失ID列表, 禁用ID列表)

        素材库删除素材后，规则里残留的旧ID不应卡住编辑保存：
        - 存在且未删除且未禁用 → 有效（保留原顺序、去重）
        - 存在但已软删除 → 静默剔除（不报错）
        - 存在但风险=禁用(2) → 禁用（由调用方明确报错，不静默剔除）
        - 不存在或不属于当前用户 → 缺失（由调用方决定报错）
        """
        valid: List[int] = []
        missing: List[int] = []
        disabled: List[int] = []
        seen: set[int] = set()
        status_map: dict[int, tuple[bool, int]] = {}
        unique_ids = list(dict.fromkeys(material_ids))
        # 分批查询：素材过多时避免 IN 子句超出 MySQL max_allowed_packet
        for i in range(0, len(unique_ids), 500):
            batch = unique_ids[i:i + 500]
            stmt = select(ProductMaterial.id, ProductMaterial.is_deleted, ProductMaterial.risk).where(
                ProductMaterial.user_id == user_id,
                ProductMaterial.id.in_(batch),
            )
            for row in (await self.session.execute(stmt)).all():
                status_map[row[0]] = (bool(row[1]), int(row[2] or 0))
        for mid in material_ids:
            if mid in seen:
                continue
            seen.add(mid)
            if mid not in status_map:
                missing.append(mid)
                continue
            is_deleted, risk = status_map[mid]
            if is_deleted:
                # 已软删除：静默剔除
                continue
            if risk == MATERIAL_RISK_DISABLED:
                disabled.append(mid)
                continue
            valid.append(mid)
        return valid, missing, disabled

    async def list_for_schedule(
        self, material_ids: List[int], user_id: int, material_scope: str = "selected",
    ) -> List[ProductMaterial]:
        """按素材范围解析定时规则的素材池

        scope=all：实时查询库内全部未删除素材（新增自动纳入、删除自动剔除）；
        scope=selected：按规则存储的素材ID快照查询（已删除的自动过滤）。
        """
        if material_scope == "all":
            material_ids = await self.list_material_ids(user_id)
        return await self.list_by_ids(material_ids, user_id)

    async def update(self, material_id: int, user_id: int = None, data: dict = None) -> Optional[ProductMaterial]:
        """更新素材（user_id=None时管理员可操作任意素材）"""
        data = data or {}
        material = await self.get(material_id, user_id)
        if not material:
            return None

        if "specifications" in data:
            data["specifications"] = _normalize_specifications(data.get("specifications"))
        if "sku_rows" in data:
            data["sku_rows"] = _normalize_sku_rows(data.get("sku_rows"))

        updatable = [
            "title", "description", "price", "original_price", "category",
            "platform_category_id", "platform_category_name",
            "platform_channel_category_id", "platform_channel_category_name",
            "platform_leaf_id", "platform_tb_category_id", "platform_category_path", "platform_attributes",
            "category_source", "category_confidence", "images", "videos", "specifications", "sku_rows", "quantity",
            "delivery_method", "shipping_method", "support_pickup", "postage", "address", "address_expected_text", "brand", "condition", "stock", "remark", "risk",
            "product_code", "versions", "default_version",
        ]
        for field in updatable:
            if field in data:
                value = data[field]
                if field in ("price", "original_price", "postage"):
                    value = float(value) if value else (None if field == "original_price" else 0)
                if field in ("stock", "risk"):
                    value = int(value)
                elif field == "default_version":
                    value = int(value) if value is not None else None
                elif field == "versions":
                    value = _normalize_versions(value)
                elif field == "support_pickup":
                    # 兼容表单/历史调用传入的布尔字符串，避免 bool("false") 被当作 True。
                    value = as_bool(value)
                setattr(material, field, value)

        # 防止历史调用只更新 delivery_method 造成与实际运费方式不一致。
        material.delivery_method = "pickup" if material.shipping_method == "none" else "express"

        await self.session.commit()
        await self.session.refresh(material)
        return material

    async def _live_material_file_basenames(self) -> set[str]:
        """收集全部未删除素材引用的本地文件名（文件在磁盘上全局共享，跨用户保护）。"""
        from common.utils.material_file_refs import extract_file_basenames

        names: set[str] = set()
        stmt = select(
            ProductMaterial.images,
            ProductMaterial.videos,
            ProductMaterial.specifications,
            ProductMaterial.versions,
        ).where(ProductMaterial.is_deleted.is_(False))
        rows = (await self.session.execute(stmt)).all()
        for images, videos, specifications, versions in rows:
            names |= extract_file_basenames(images, videos, specifications, versions)
        return names

    def _cleanup_material_files(
        self, deleted_names: set[str], live_names: set[str]
    ) -> None:
        """删除素材后级联清理本地文件（仅删不再被任何有效素材引用的文件）。

        uploads/products/ 与单品发布上传共用，因此只删除「本次删除素材引用、
        且没有任何未删除素材引用」的文件；删除失败仅记日志不影响主流程。
        注意：素材行已物理删除，deleted_names 须在删行前收集好。
        """
        from common.utils.image_utils import delete_static_file

        removed = 0
        for name in sorted(deleted_names - live_names):
            if delete_static_file(f"/static/uploads/products/{name}"):
                removed += 1
        # 删除后失效本进程目录体积缓存，存储分布回退统计立即反映新体积
        if removed:
            try:
                from common.services.system_metrics import invalidate_dir_size_cache

                invalidate_dir_size_cache()
            except Exception:
                pass

    def _collect_material_file_basenames(
        self, materials: List[ProductMaterial]
    ) -> set[str]:
        """收集素材全字段（images/videos/specifications/versions）引用的本地文件名。"""
        from common.utils.material_file_refs import extract_file_basenames

        names: set[str] = set()
        for material in materials:
            names |= extract_file_basenames(
                material.images, material.videos, material.specifications, material.versions
            )
        return names

    async def delete(self, material_id: int, user_id: int = None) -> bool:
        """物理删除素材（user_id=None时管理员可操作任意素材）

        级联顺序：关闭自动续售规则（规则关闭流程需要素材行）→ 删行前收集文件引用
        → 物理删除 → 清理仅被本素材引用的本地文件。
        """
        from app.services.auto_relist_rule_service import AutoRelistRuleService

        material = await self.get(material_id, user_id)
        if not material:
            return False
        # 先关规则：close_rule 内部 commit，规则关闭流程依赖素材行仍存在
        await AutoRelistRuleService(self.session).close_rule(material.id, material.user_id)
        deleted_names = self._collect_material_file_basenames([material])
        await self.session.delete(material)
        await self.session.commit()
        # 级联清理本地文件（仅删不再被任何有效素材引用的文件，best-effort）
        live_names = await self._live_material_file_basenames()
        self._cleanup_material_files(deleted_names, live_names)
        return True

    async def batch_delete(self, material_ids: List[int], user_id: int = None) -> int:
        """批量物理删除素材，返回实际删除数量

        Args:
            material_ids: 素材ID列表
            user_id: 用户ID，为None时管理员可操作任意素材
        """
        from app.services.auto_relist_rule_service import AutoRelistRuleService

        if not material_ids:
            return 0
        conds = [ProductMaterial.id.in_(material_ids), ProductMaterial.is_deleted.is_(False)]
        if user_id is not None:
            conds.append(ProductMaterial.user_id == user_id)
        stmt = select(ProductMaterial).where(*conds)
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return 0
        # 先关规则（逐条，close_rule 内部 commit），再收集文件引用，最后物理删行
        relist_service = AutoRelistRuleService(self.session)
        for row in rows:
            await relist_service.close_rule(row.id, row.user_id)
        deleted_names = self._collect_material_file_basenames(list(rows))
        for row in rows:
            await self.session.delete(row)
        await self.session.commit()
        # 级联清理本地文件（仅删不再被任何有效素材引用的文件，best-effort）
        live_names = await self._live_material_file_basenames()
        self._cleanup_material_files(deleted_names, live_names)
        return len(rows)


# ==================== 工具函数 ====================

def _material_to_dict(m: ProductMaterial) -> dict:
    """将素材模型转为字典"""
    shipping_method = m.shipping_method or ("fixed" if m.postage else "free")
    return {
        "id": m.id,
        "user_id": m.user_id,
        "title": m.title,
        "description": m.description,
        "price": float(m.price) if m.price is not None else 0,
        "original_price": float(m.original_price) if m.original_price is not None else None,
        "category": m.category,
        "platform_category_id": m.platform_category_id,
        "platform_category_name": m.platform_category_name,
        "platform_channel_category_id": m.platform_channel_category_id,
        "platform_channel_category_name": m.platform_channel_category_name,
        "platform_leaf_id": m.platform_leaf_id,
        "platform_tb_category_id": m.platform_tb_category_id,
        "platform_category_path": m.platform_category_path or [],
        "platform_attributes": m.platform_attributes or [],
        "category_source": m.category_source or "manual",
        "category_confidence": float(m.category_confidence) if m.category_confidence is not None else None,
        "images": m.images or [],
        "videos": m.videos or [],
        "specifications": m.specifications or [],
        "sku_rows": m.sku_rows or [],
        "quantity": m.quantity or 1,
        # shipping_method 是实际发布依据；兼容历史记录中的旧/空 delivery_method。
        "delivery_method": "pickup" if shipping_method == "none" else "express",
        "shipping_method": shipping_method,
        "support_pickup": as_bool(m.support_pickup),
        "postage": float(m.postage) if m.postage is not None else 0,
        "address": m.address,
        "address_expected_text": m.address_expected_text,
        "brand": m.brand,
        "condition": m.condition,
        "stock": int(m.stock) if m.stock is not None else 9999,
        "remark": m.remark,
        "risk": int(m.risk) if m.risk is not None else 0,
        "product_code": m.product_code,
        "versions": m.versions or [],
        "default_version": int(m.default_version) if m.default_version is not None else None,
        "created_at": safe_isoformat(m.created_at),
        "updated_at": safe_isoformat(m.updated_at),
    }
