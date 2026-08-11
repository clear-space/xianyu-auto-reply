"""
闲鱼发布器 — 直接使用 PromotionXianyuPublisher（卖家端 seller.goofish.com）
已验证库存设置/分类选择/发布流程全部可用，不做任何封装层。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common.services.promotion_xianyu_publisher import PromotionXianyuPublisher


def create_xianyu_publisher(static_root: str | Path | None = None) -> "PatchedPublisher":
    """创建发布器实例 — PatchedPublisher 继承 PromotionXianyuPublisher，优先「电子资料」分类。"""
    return PatchedPublisher(static_root=static_root)


async def publish_single_item(
    item_data: dict,
    cookie: str,
    static_root: str | Path | None = None,
) -> dict:
    """执行一次单品发布。"""
    publisher = create_xianyu_publisher(static_root=static_root)
    return await publisher.publish_item(
        item_data=item_data,
        cookie_data={"cookie": cookie},
        reuse_browser=False,
        should_close=True,
    )


class PatchedPublisher(PromotionXianyuPublisher):
    """在卖家中心分类选择中优先匹配「电子资料」。"""

    async def _get_leaf_category_options(
        self, container: Any = None, exclude_texts: set[str] | None = None
    ):
        options = await super()._get_leaf_category_options(container, exclude_texts)
        if not options:
            return options
        for i in range(len(options)):
            if "电子资料" in options[i][0]:
                options.append(options.pop(i))
                break
        return options
