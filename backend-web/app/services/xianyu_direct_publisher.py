"""
闲鱼单品接口发布服务。

功能：
1. 复用公共载荷构造器组装 idleitem.publish 请求；
2. 调用发布接口并解析商品ID/商品链接；
3. 复用公共 mtop 客户端的令牌刷新、Cookie 回写和风控识别。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.xianyu_direct_payload import (
    DirectPublishError,
    extract_item_id_from_url as _extract_item_id_from_url,
    find_item_reference as _find_item_reference,
    text as _text,
)
from app.services.xianyu_item_payload_builder import (
    _build_attribute_labels,
    build_category_info,
    build_item_payload,
)
from common.services.xianyu_mtop import mtop_call
from common.utils.xianyu_utils import canonical_goofish_item_url


PUBLISH_API = "mtop.idle.pc.backend.idleitem.publish"
SELLER_ORIGIN = "https://seller.goofish.com"
SELLER_REFERER = "https://seller.goofish.com/?site=COMMONPRO"

# 内置兜底分类常量从共享模块导入，批量导入与新建素材共用同一默认值。
from app.services.platform_category_defaults import DEFAULT_PLATFORM_CATEGORIES  # noqa: E402


def _category_missing(item_data: dict[str, Any]) -> bool:
    """平台分类信息是否缺失。

    只以频道分类ID为判据：推荐接口对大量类目不返回 catId（学习资料定制、
    手办等），用户已选的分类即使没有 catId 也不应被默认分类覆盖。
    """
    return not _text(item_data.get("platform_channel_category_id"))


def _apply_default_category(item_data: dict[str, Any], default: dict[str, Any]) -> None:
    """将内置默认分类写入商品数据。"""
    item_data["platform_category_id"] = default.get("cat_id") or ""
    item_data["platform_category_name"] = default["name"]
    item_data["platform_channel_category_id"] = default["channel_cat_id"]
    item_data["platform_channel_category_name"] = default["name"]
    item_data["platform_leaf_id"] = ""
    item_data["platform_tb_category_id"] = ""
    item_data["platform_category_path"] = [
        {"id": default.get("cat_id") or default["channel_cat_id"], "name": default["name"]}
    ]



class XianyuDirectPublisher:
    """使用闲鱼卖家工作台 mtop 接口发布单个商品。"""

    def __init__(self, static_root: str | Path | None = None):
        self.static_root = Path(static_root) if static_root else None

    async def publish_item(
        self,
        item_data: dict[str, Any],
        cookie: str,
        account_id: str,
        owner_id: int | None,
    ) -> dict[str, Any]:
        """上传媒体并调用最终发布接口，返回统一发布结果。"""
        # 未选择平台分类时，按内置兜底分类补全（优先电子资料）。
        applied_default: dict[str, Any] | None = None
        if _category_missing(item_data):
            applied_default = DEFAULT_PLATFORM_CATEGORIES[0]
            _apply_default_category(item_data, applied_default)
            logger.info(
                f"商品未选择平台分类，默认使用[{applied_default['name']}]发布: account_id={account_id}"
            )

        try:
            payload, cookie = await build_item_payload(
                item_data,
                cookie,
                account_id,
                owner_id,
                static_root=self.static_root,
            )
        except DirectPublishError as exc:
            if not exc.account_invalid:
                raise
            # 媒体上传时判定账号失效：保持原有返回结构，交由上层标记账号状态。
            return {
                "success": False,
                "message": str(exc),
                "item_id": None,
                "item_url": None,
                "account_invalid": True,
                "cookies_str": cookie,
            }

        async def _call(payload_to_send: dict[str, Any]) -> dict[str, Any]:
            return await mtop_call(
                account_id=account_id,
                cookies_str=cookie,
                api=PUBLISH_API,
                version="1.0",
                data={"inputJson": json.dumps(payload_to_send, ensure_ascii=False, separators=(",", ":"))},
                owner_id=owner_id,
                extra_params={
                    "idle_site_biz_code": "COMMONPRO",
                    "spm_cnt": "a21107h.42826273.0.0",
                },
                origin=SELLER_ORIGIN,
                referer=SELLER_REFERER,
                extra_headers={"idle_site_biz_code": "COMMONPRO"},
            )

        response = await _call(payload)
        if not response.get("success") and applied_default is not None:
            # 自动补全的默认分类被平台拒绝时，依次回退到下一个兜底分类重试一次；
            # 用户显式选择的分类不参与回退，避免覆盖用户意图。
            default_index = DEFAULT_PLATFORM_CATEGORIES.index(applied_default)
            if default_index + 1 < len(DEFAULT_PLATFORM_CATEGORIES):
                fallback = DEFAULT_PLATFORM_CATEGORIES[default_index + 1]
                logger.warning(
                    f"默认分类[{applied_default['name']}]发布失败，回退[{fallback['name']}]重试: "
                    f"account_id={account_id}, error={response.get('error')}"
                )
                _apply_default_category(item_data, fallback)
                # 仅重建受分类影响的载荷片段，不重复上传图片/视频。
                payload["itemLabelExtList"] = _build_attribute_labels(item_data)
                payload["itemCatDTO"] = build_category_info(item_data)
                response = await _call(payload)

        if not response.get("success"):
            logger.error(
                f"闲鱼商品发布接口失败完整返回: account_id={account_id}, "
                f"response={json.dumps(response, ensure_ascii=False, default=str)}"
            )
            return {
                "success": False,
                "message": f"闲鱼接口发布失败：{response.get('error') or '未知错误'}",
                "item_id": None,
                "item_url": None,
                "account_invalid": bool(response.get("account_invalid")),
                "cookies_str": response.get("cookies_str") or cookie,
            }
        item_id, item_url = _find_item_reference(response.get("res"))
        if item_id:
            # 发布接口可能返回旧版 /item/{id} 或带协议地址，统一改成当前网页格式。
            item_url = canonical_goofish_item_url(item_id)
        elif item_url:
            extracted_id = _extract_item_id_from_url(item_url)
            if extracted_id:
                item_id = extracted_id
                item_url = canonical_goofish_item_url(extracted_id)
        logger.info(f"闲鱼商品发布接口调用成功: account_id={account_id}, item_id={item_id or '未返回'}")
        return {
            "success": True,
            "message": "商品发布成功",
            "item_id": item_id,
            "item_url": item_url,
            "account_invalid": False,
            "cookies_str": response.get("cookies_str") or cookie,
        }


__all__ = ["DirectPublishError", "XianyuDirectPublisher"]
