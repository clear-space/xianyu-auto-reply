"""
历史软删素材行一次性物理清除

素材删除已改为物理删除（硬删除），本模块负责清空升级前遗留的 is_deleted=1 素材行：
1. 先按行内引用清理其独占的本地文件（uploads/products/）——行是文件引用的唯一依据，
   必须在删行之前完成，否则这些文件永远失去引用无法回收；
2. 关闭仍启用的自动续售规则（规则关闭/对账流程依赖素材行存在，行删掉后无法关闭）；
3. 分批物理删除行，释放 uk_pm_user_code(user_id, product_code) 唯一索引槽位
   （此前软删行占着槽位，删除后重新导入同编号会报 Duplicate entry）。

挂接在 backend-web 启动初始化（init_database.init_all），幂等：无软删行时 no-op。
"""
from __future__ import annotations

from typing import Dict, List, Set

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.auto_relist_event import AutoRelistEvent
from common.models.auto_relist_rule import AutoRelistRule
from common.models.product_material import ProductMaterial
from common.utils.image_utils import delete_static_file
from common.utils.material_file_refs import extract_file_basenames

# 单批删除行数（行很小，1000 一批足够，批间由 commit 天然让出）
PURGE_BATCH_SIZE = 1000


def _collect_file_basenames(rows) -> Set[str]:
    """从行集提取本地文件名（行可为 (id, images, videos, specifications, versions)
    五元组或 (images, videos, specifications, versions) 四元组）。"""
    names: Set[str] = set()
    for row in rows:
        images, videos, specifications, versions = row[-4:]
        names |= extract_file_basenames(images, videos, specifications, versions)
    return names


async def purge_soft_deleted_materials(session: AsyncSession) -> Dict[str, int]:
    """清除全部软删素材行（先清文件 → 关规则 → 删行），返回统计 dict。

    Args:
        session: 已初始化的数据库会话（由 init_database.init_all 传入）

    Returns:
        {"found": 发现的软删行数, "deleted": 实际删除行数,
         "files_cleaned": 清理的文件数, "rules_closed": 关闭的规则数}
    """
    stats: Dict[str, int] = {
        "found": 0,
        "deleted": 0,
        "files_cleaned": 0,
        "rules_closed": 0,
    }

    # 1. 读取全部软删行与活素材引用（文件在磁盘上全局共享，跨用户保护）
    deleted_fields = (
        await session.execute(
            select(
                ProductMaterial.id,
                ProductMaterial.images,
                ProductMaterial.videos,
                ProductMaterial.specifications,
                ProductMaterial.versions,
            ).where(ProductMaterial.is_deleted.is_(True))
        )
    ).all()
    stats["found"] = len(deleted_fields)
    if not deleted_fields:
        return stats

    live_fields = (
        await session.execute(
            select(
                ProductMaterial.images,
                ProductMaterial.videos,
                ProductMaterial.specifications,
                ProductMaterial.versions,
            ).where(ProductMaterial.is_deleted.is_(False))
        )
    ).all()
    live_names = _collect_file_basenames(live_fields)
    deleted_names = _collect_file_basenames(deleted_fields)

    # 2. 先清文件（仅删不再被任何有效素材引用的文件，best-effort）
    for name in sorted(deleted_names - live_names):
        if delete_static_file(f"/static/uploads/products/{name}"):
            stats["files_cleaned"] += 1

    deleted_ids: List[int] = [int(row[0]) for row in deleted_fields]

    # 3. 关闭仍启用的自动续售规则（其关闭/对账流程依赖素材行存在）
    rule_ids = (
        await session.execute(
            select(AutoRelistRule.id).where(
                AutoRelistRule.material_id.in_(deleted_ids),
                AutoRelistRule.enabled.is_(True),
            )
        )
    ).scalars().all()
    if rule_ids:
        await session.execute(
            update(AutoRelistRule)
            .where(AutoRelistRule.id.in_(list(rule_ids)))
            .values(enabled=False, status="disabled", next_retry_at=None, paused_reason=None)
        )
        await session.execute(
            update(AutoRelistEvent)
            .where(
                AutoRelistEvent.rule_id.in_(list(rule_ids)),
                AutoRelistEvent.status.in_(["pending", "retry", "claimed", "checking"]),
            )
            .values(
                status="skipped",
                error_message="素材已删除，自动续售已关闭",
                next_retry_at=None,
                claim_token=None,
                claimed_at=None,
                lease_expires_at=None,
            )
        )
        stats["rules_closed"] = len(rule_ids)
        await session.commit()

    # 4. 分批物理删除行
    for start in range(0, len(deleted_ids), PURGE_BATCH_SIZE):
        batch = deleted_ids[start : start + PURGE_BATCH_SIZE]
        result = await session.execute(
            delete(ProductMaterial).where(
                ProductMaterial.id.in_(batch),
                ProductMaterial.is_deleted.is_(True),
            )
        )
        stats["deleted"] += int(result.rowcount or 0)
        await session.commit()

    logger.info(
        f"软删素材物理清除完成：发现 {stats['found']} 行，删除 {stats['deleted']} 行，"
        f"清理文件 {stats['files_cleaned']} 个，关闭续售规则 {stats['rules_closed']} 条"
    )
    return stats
