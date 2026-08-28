"""
统一卡券匹配服务

功能：
1. 提供统一的卡券查询方法（通过关联表查询，含向后兼容回退）
2. 提供统一的规格匹配逻辑（完全匹配 > 名称匹配 > 通用卡券）
3. 提供批量查询商品卡券配置状态
4. 被 backend-web、websocket、scheduler 三个服务统一调用

匹配优先级：
- 完全匹配：spec_name + spec_value 都匹配
- 名称匹配：仅 spec_name 匹配（暂未启用，保留扩展）
- 通用卡券：is_multi_spec=False 的卡券
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.services.item_service import _normalize_item_status


from common.utils.time_utils import safe_isoformat
class CardMatcher:
    """统一卡券匹配器"""

    def __init__(self, session: AsyncSession):
        """
        初始化卡券匹配器
        
        Args:
            session: 异步数据库会话
        """
        self.session = session

    async def get_cards_by_item_id(
        self,
        item_id: str,
        spec_name: Optional[str] = None,
        spec_value: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        根据商品ID获取匹配的卡券列表（统一入口）
        
        查询顺序：
        1. 优先从 xy_card_item_relations 关联表查询（含 source/dock_record_id）
        2. 关联表无数据时，回退到 xy_cards.item_id 字段（向后兼容）
        3. 对查询结果进行规格匹配过滤
        
        注意：同一个 card_id 可能有多条关联记录（不同 source），每条都会单独返回。
        
        Args:
            item_id: 商品ID
            spec_name: 规格名称（可选，用于多规格匹配）
            spec_value: 规格值（可选，用于多规格匹配）
            
        Returns:
            匹配的卡券字典列表（每条含 card_source 和 dock_record_id）
        """
        # 1. 优先从关联表查询（返回 Card+source+dock_record_id 元组，不去重）
        relation_rows = await self._query_cards_with_source(item_id)
        
        if relation_rows:
            # 关联表有数据：每行转为字典，附带 source 信息
            all_cards = []
            for card, card_source, dock_record_id in relation_rows:
                card_dict = self._card_to_dict(card)
                card_dict["card_source"] = card_source or "own"
                card_dict["dock_record_id"] = dock_record_id
                all_cards.append(card_dict)
            
            # 规格匹配过滤（对字典列表过滤）
            matched = self._match_card_dicts_by_spec(all_cards, spec_name, spec_value)
            # 按 card.id 去重（发货场景下同一张卡券视为一张）：
            # 关联表可能因历史数据冗余或对接关系存在多条同 card_id 记录
            matched = self._dedup_cards_by_id(matched)
            logger.info(
                f"卡券匹配: item_id={item_id}, 来源=关联表, "
                f"查询到={len(all_cards)}条, 规格过滤/去重后={len(matched)}张, "
                f"spec_name={spec_name}, spec_value={spec_value}"
            )
            return matched
        
        # 2. 关联表无数据，回退到旧字段
        legacy_cards = await self._query_cards_from_legacy(item_id)
        if not legacy_cards:
            logger.info(f"卡券匹配: item_id={item_id}, 未找到任何卡券")
            return []
        
        matched = self._match_cards_by_spec(legacy_cards, spec_name, spec_value)
        for card_dict in matched:
            card_dict["card_source"] = "own"
            card_dict["dock_record_id"] = None
        # 旧字段来源理论上不会重复，但保持一致行为
        matched = self._dedup_cards_by_id(matched)
        
        logger.info(
            f"卡券匹配: item_id={item_id}, 来源=旧字段, "
            f"查询到={len(legacy_cards)}张, 规格过滤/去重后={len(matched)}张, "
            f"spec_name={spec_name}, spec_value={spec_value}"
        )
        return matched

    @staticmethod
    def _dedup_cards_by_id(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 card.id 去重，优先保留 card_source='own' 的记录
        
        场景：同一张卡券可能因历史数据冗余或对接关系在关联表有多条记录，
        发货/匹配场景下应视为同一张卡券，避免误报"多卡冲突"。
        
        Args:
            cards: 卡券字典列表
            
        Returns:
            去重后的卡券字典列表（保持原顺序，但 own 源优先）
        """
        # 第一轮：收集每个 card_id 的最优记录（own 优先，否则首个）
        best_by_id: Dict[Any, Dict[str, Any]] = {}
        order: List[Any] = []
        for c in cards:
            cid = c.get("id")
            if cid is None:
                continue
            source = c.get("card_source") or "own"
            if cid not in best_by_id:
                best_by_id[cid] = c
                order.append(cid)
            elif source == "own" and best_by_id[cid].get("card_source") != "own":
                # 遇到 own 源，替换之前的非 own 记录
                best_by_id[cid] = c
        return [best_by_id[cid] for cid in order]

    async def get_all_cards_by_item_id(self, item_id: str) -> List[Dict[str, Any]]:
        """
        获取商品关联的所有卡券（管理展示用，不过滤启用状态和规格）
        
        与 get_cards_by_item_id 不同，此方法：
        1. 不过滤 Card.enabled，返回启用和禁用的卡券
        2. 不做规格匹配，返回所有规格的卡券
        3. 返回关联表中的 source 和 dock_record_id
        
        Args:
            item_id: 商品ID
            
        Returns:
            所有关联的卡券字典列表（含 card_source, dock_record_id）
        """
        # 1. 优先从关联表查询（不过滤 enabled），同时取出 source 和 dock_record_id
        stmt = (
            select(Card, CardItemRelation.source, CardItemRelation.dock_record_id)
            .join(
                CardItemRelation,
                Card.id == CardItemRelation.card_id,
            )
            .where(CardItemRelation.item_id == item_id)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        
        if rows:
            cards_out = []
            for row in rows:
                card_dict = self._card_to_dict(row[0])
                card_dict["card_source"] = row[1] or "own"
                card_dict["dock_record_id"] = row[2]
                cards_out.append(card_dict)
            return cards_out
        
        # 2. 关联表无数据，回退到旧字段（不过滤 enabled）
        legacy_stmt = select(Card).where(Card.item_id == item_id)
        legacy_result = await self.session.execute(legacy_stmt)
        cards = list(legacy_result.scalars().all())
        
        result_list = []
        for card in cards:
            card_dict = self._card_to_dict(card)
            card_dict["card_source"] = "own"
            card_dict["dock_record_id"] = None
            result_list.append(card_dict)
        return result_list

    async def get_card_item_ids(self, card_id: int) -> List[str]:
        """
        获取卡券关联的所有商品ID列表
        
        Args:
            card_id: 卡券ID
            
        Returns:
            商品ID列表
        """
        stmt = select(CardItemRelation.item_id).where(
            CardItemRelation.card_id == card_id
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_items_with_card_status(
        self,
        item_ids: List[str],
    ) -> Dict[str, bool]:
        """
        批量查询商品是否配置了卡券（不区分用户，与发货配置弹窗查询逻辑一致）
        
        Args:
            item_ids: 商品ID列表
            
        Returns:
            {item_id: True/False} 字典
        """
        if not item_ids:
            return {}
        
        # 从关联表查询
        relation_items: set = set()
        try:
            stmt = select(CardItemRelation.item_id).where(
                CardItemRelation.item_id.in_(item_ids),
            ).distinct()
            result = await self.session.execute(stmt)
            relation_items = {row[0] for row in result.all()}
        except Exception as e:
            logger.warning(f"从关联表查询卡券状态失败（回退到旧字段）: {e}")
        
        # 从旧字段查询（向后兼容）
        legacy_stmt = select(Card.item_id).where(
            Card.item_id.in_(item_ids),
            Card.enabled == True,
        ).distinct()
        legacy_result = await self.session.execute(legacy_stmt)
        legacy_items = {row[0] for row in legacy_result.all() if row[0]}
        
        # 合并结果
        configured_items = relation_items | legacy_items
        logger.info(f"卡券状态查询: 查询商品数={len(item_ids)}, 关联表命中={len(relation_items)}, 旧字段命中={len(legacy_items)}, 总命中={len(configured_items)}")
        return {item_id: item_id in configured_items for item_id in item_ids}

    async def update_card_item_relations(
        self,
        card_id: int,
        user_id: int,
        item_ids: List[str],
    ) -> Dict[str, int]:
        """
        更新卡券的商品关联关系（先删旧关联再插新关联，同一事务）
        
        Args:
            card_id: 卡券ID
            user_id: 用户ID
            item_ids: 新的商品ID列表
            
        Returns:
            {"added": 新增数量, "removed": 删除数量}
        """
        # 删除旧关联
        delete_result = await self.session.execute(
            text("DELETE FROM xy_card_item_relations WHERE card_id = :card_id"),
            {"card_id": card_id}
        )
        removed = delete_result.rowcount
        
        # 插入新关联
        added = 0
        for item_id in item_ids:
            if not item_id:
                continue
            await self.session.execute(
                text("""
                    INSERT IGNORE INTO xy_card_item_relations 
                    (user_id, card_id, item_id, dock_record_id, created_at, updated_at)
                    VALUES (:user_id, :card_id, :item_id, 0, NOW(), NOW())
                """),
                {"user_id": user_id, "card_id": card_id, "item_id": item_id}
            )
            added += 1
        
        await self.session.flush()
        return {"added": added, "removed": removed}

    async def update_item_card_relations(
        self,
        item_id: str,
        user_id: int,
        card_relations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, int]:
        """
        更新商品关联的卡券列表（先删旧关联再插新关联）
        
        Args:
            item_id: 商品ID
            user_id: 用户ID
            card_relations: 卡券关联列表，每个元素含 card_id, source, dock_record_id
            
        Returns:
            {"added": 新增数量, "removed": 删除数量}
        """
        # 删除旧关联
        delete_result = await self.session.execute(
            text("DELETE FROM xy_card_item_relations WHERE item_id = :item_id"),
            {"item_id": item_id}
        )
        removed = delete_result.rowcount
        
        # 插入新关联（允许同一 card_id 多条记录，通过 source+dock_record_id 区分）
        added = 0
        for rel in (card_relations or []):
            card_id = rel.get("card_id")
            if not card_id:
                continue
            source = rel.get("source", "own")
            dock_record_id = rel.get("dock_record_id") or 0
            await self.session.execute(
                text("""
                    INSERT INTO xy_card_item_relations 
                    (user_id, card_id, item_id, source, dock_record_id, created_at, updated_at)
                    VALUES (:user_id, :card_id, :item_id, :source, :dock_record_id, NOW(), NOW())
                """),
                {"user_id": user_id, "card_id": card_id, "item_id": item_id,
                 "source": source, "dock_record_id": dock_record_id}
            )
            added += 1
        
        await self.session.flush()
        return {"added": added, "removed": removed}

    async def batch_bind_cards_to_items(
        self,
        user_id: int,
        card_ids: List[int],
        item_ids: List[str],
    ) -> Dict[str, int]:
        """
        批量绑定卡券到商品（INSERT IGNORE 避免重复）
        
        Args:
            user_id: 用户ID
            card_ids: 卡券ID列表
            item_ids: 商品ID列表
            
        Returns:
            {"success_count": 成功数量, "fail_count": 失败数量}
        """
        success_count = 0
        fail_count = 0
        
        for card_id in card_ids:
            for item_id in item_ids:
                if not item_id:
                    continue
                try:
                    result = await self.session.execute(
                        text("""
                            INSERT IGNORE INTO xy_card_item_relations 
                            (user_id, card_id, item_id, dock_record_id, created_at, updated_at)
                            VALUES (:user_id, :card_id, :item_id, 0, NOW(), NOW())
                        """),
                        {"user_id": user_id, "card_id": card_id, "item_id": item_id}
                    )
                    if result.rowcount > 0:
                        success_count += 1
                except Exception as e:
                    logger.warning(f"绑定卡券 {card_id} 到商品 {item_id} 失败: {e}")
                    fail_count += 1
        
        await self.session.flush()
        return {"success_count": success_count, "fail_count": fail_count}

    @staticmethod
    def _collect_pairs_by_prefix(
        card_map: Dict[tuple, List[Card]],
        item_map: Dict[tuple, List[str]],
        existing_pairs: set,
    ) -> tuple[List[tuple], Dict[str, Any]]:
        """按前缀编号配对：同编号全部配对，已存在的 (card_id, item_id) 跳过。

        Returns:
            (待插入 (card_id, item_id) 列表, 统计部分 dict)
        """
        pairs: List[tuple] = []
        matched_cards = 0
        matched_pairs = 0
        cards_no_match = 0
        no_match_names: List[str] = []
        for key, cs in card_map.items():
            item_ids = item_map.get(key)
            if not item_ids:
                cards_no_match += len(cs)
                for c in cs:
                    if len(no_match_names) < 50:
                        no_match_names.append(c.name)
                continue
            matched_cards += len(cs)
            for c in cs:
                for iid in item_ids:
                    matched_pairs += 1
                    if (c.id, iid) not in existing_pairs:
                        pairs.append((c.id, iid))
        return pairs, {
            "matched_cards": matched_cards,
            "matched_pairs": matched_pairs,
            "cards_no_match": cards_no_match,
            "no_match_names": no_match_names,
        }

    async def _insert_pairs_batched(self, user_id: int, pairs: List[tuple]) -> int:
        """分批 INSERT IGNORE 插入关联对（500 对/批），返回实际新增数"""
        added = 0
        batch_size = 500
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            values_sql = ", ".join(
                f"(:uid_{j}, :cid_{j}, :iid_{j}, 0, NOW(), NOW())"
                for j in range(len(batch))
            )
            params: Dict[str, Any] = {}
            for j, (card_id, item_id) in enumerate(batch):
                params[f"uid_{j}"] = user_id
                params[f"cid_{j}"] = card_id
                params[f"iid_{j}"] = item_id
            result = await self.session.execute(
                text(f"""
                    INSERT IGNORE INTO xy_card_item_relations
                    (user_id, card_id, item_id, dock_record_id, created_at, updated_at)
                    VALUES {values_sql}
                """),
                params,
            )
            added += result.rowcount or 0
        return added

    async def match_cards_by_prefix_number(self, user_id: int) -> Dict[str, Any]:
        """一键关联卡券：按前缀编号（字母+三位数字，如 A014）匹配商品标题与卡券名称。

        规则：
        - 仅处理启用卡券；禁用卡券计入 disabled_cards 跳过
        - 已存在的 (card_id, item_id) 关联自动跳过（不区分 source/dock_record_id）
        - 编号相同的卡券与商品全部配对（多对多）
        - 分批 INSERT IGNORE（500 对/批），单事务，幂等（重复执行 added=0）

        Returns:
            {"matched_cards", "matched_pairs", "added", "skipped",
             "cards_no_number", "cards_no_match", "disabled_cards",
             "no_number_names": [...], "no_match_names": [...]}  # 明细最多 50 条
        """
        from common.models.xy_catalog_item import XYCatalogItem
        from common.utils.text_utils import extract_prefix_number

        stats: Dict[str, Any] = {
            "matched_cards": 0,
            "matched_pairs": 0,
            "added": 0,
            "skipped": 0,
            "cards_no_number": 0,
            "cards_no_match": 0,
            "disabled_cards": 0,
            "no_number_names": [],
            "no_match_names": [],
        }

        # 1. 查用户全部卡券（启用状态在内存区分）
        cards = list(
            (await self.session.execute(
                select(Card).where(Card.user_id == user_id)
            )).scalars().all()
        )

        card_map: Dict[tuple, List[Card]] = {}
        for card in cards:
            if not card.enabled:
                stats["disabled_cards"] += 1
                continue
            key = extract_prefix_number(card.name)
            if key is None:
                stats["cards_no_number"] += 1
                if len(stats["no_number_names"]) < 50:
                    stats["no_number_names"].append(card.name)
                continue
            card_map.setdefault(key, []).append(card)

        if not card_map:
            await self.session.commit()
            return stats

        # 2. 查用户全部商品，按编号分组（已删除商品不参与关联）
        item_rows = (
            await self.session.execute(
                select(XYCatalogItem.item_id, XYCatalogItem.title, XYCatalogItem.metadata_json).where(
                    XYCatalogItem.owner_id == user_id
                )
            )
        ).all()
        item_map: Dict[tuple, List[str]] = {}
        for item_id, title, meta in item_rows:
            if _normalize_item_status((meta or {}).get("item_status")) == "deleted":
                continue
            key = extract_prefix_number(title)
            if key is not None:
                item_map.setdefault(key, []).append(item_id)

        # 3. 已存在关联集合（已有关联即跳过，不区分来源）
        existing_pairs: set = set()
        if item_map:
            existing_rows = (
                await self.session.execute(
                    select(CardItemRelation.card_id, CardItemRelation.item_id).where(
                        CardItemRelation.user_id == user_id
                    )
                )
            ).all()
            existing_pairs = {(card_id, item_id) for card_id, item_id in existing_rows}

        # 4. 配对 + 5. 分批插入（共享逻辑）
        pairs, pair_stats = self._collect_pairs_by_prefix(card_map, item_map, existing_pairs)
        stats.update(pair_stats)
        stats["skipped"] = stats["matched_pairs"] - len(pairs)
        stats["added"] = await self._insert_pairs_batched(user_id, pairs)

        await self.session.commit()
        logger.info(
            f"一键关联卡券: user_id={user_id}, 匹配卡券={stats['matched_cards']}, "
            f"配对={stats['matched_pairs']}, 新增={stats['added']}, 跳过={stats['skipped']}, "
            f"无编号={stats['cards_no_number']}, 无匹配={stats['cards_no_match']}, "
            f"禁用={stats['disabled_cards']}"
        )
        return stats

    async def match_cards_for_item_ids(self, user_id: int, item_ids: List[str]) -> Dict[str, int]:
        """按前缀编号将启用卡券与指定商品自动配对（新商品入库钩子使用）。

        幂等：已存在关联跳过；单事务；静默执行（不做无编号/禁用统计）。
        Returns: {"matched_cards", "matched_pairs", "added", "skipped"}
        """
        from common.models.xy_catalog_item import XYCatalogItem
        from common.utils.text_utils import extract_prefix_number

        empty = {"matched_cards": 0, "matched_pairs": 0, "added": 0, "skipped": 0}
        cleaned_ids = list(dict.fromkeys(iid for iid in (item_ids or []) if iid))
        if not cleaned_ids:
            return empty

        cards = list(
            (await self.session.execute(
                select(Card).where(Card.user_id == user_id, Card.enabled == True)
            )).scalars().all()
        )
        card_map: Dict[tuple, List[Card]] = {}
        for card in cards:
            key = extract_prefix_number(card.name)
            if key is not None:
                card_map.setdefault(key, []).append(card)
        if not card_map:
            await self.session.commit()
            return empty

        item_rows = (
            await self.session.execute(
                select(XYCatalogItem.item_id, XYCatalogItem.title, XYCatalogItem.metadata_json).where(
                    XYCatalogItem.owner_id == user_id,
                    XYCatalogItem.item_id.in_(cleaned_ids),
                )
            )
        ).all()
        item_map: Dict[tuple, List[str]] = {}
        for item_id, title, meta in item_rows:
            if _normalize_item_status((meta or {}).get("item_status")) == "deleted":
                continue
            key = extract_prefix_number(title)
            if key is not None:
                item_map.setdefault(key, []).append(item_id)
        if not item_map:
            await self.session.commit()
            return empty

        existing_rows = (
            await self.session.execute(
                select(CardItemRelation.card_id, CardItemRelation.item_id).where(
                    CardItemRelation.user_id == user_id,
                    CardItemRelation.item_id.in_(cleaned_ids),
                )
            )
        ).all()
        existing_pairs = {(card_id, item_id) for card_id, item_id in existing_rows}

        pairs, pair_stats = self._collect_pairs_by_prefix(card_map, item_map, existing_pairs)
        added = await self._insert_pairs_batched(user_id, pairs)
        await self.session.commit()
        return {
            "matched_cards": pair_stats["matched_cards"],
            "matched_pairs": pair_stats["matched_pairs"],
            "added": added,
            "skipped": pair_stats["matched_pairs"] - len(pairs),
        }

    async def delete_relations_by_card_id(self, card_id: int) -> int:
        """
        删除卡券的所有关联记录（级联删除）
        
        Args:
            card_id: 卡券ID
            
        Returns:
            删除的记录数
        """
        result = await self.session.execute(
            text("DELETE FROM xy_card_item_relations WHERE card_id = :card_id"),
            {"card_id": card_id}
        )
        return result.rowcount

    async def delete_relations_by_item_id(self, item_id: str) -> int:
        """
        删除商品的所有关联记录（级联删除）
        
        Args:
            item_id: 商品ID
            
        Returns:
            删除的记录数
        """
        result = await self.session.execute(
            text("DELETE FROM xy_card_item_relations WHERE item_id = :item_id"),
            {"item_id": item_id}
        )
        return result.rowcount

    async def delete_relation_by_card_and_item(self, card_id: int, item_id: str) -> bool:
        """
        删除指定卡券与指定商品的关联记录
        
        Args:
            card_id: 卡券ID
            item_id: 商品ID
            
        Returns:
            是否成功删除
        """
        result = await self.session.execute(
            text("DELETE FROM xy_card_item_relations WHERE card_id = :card_id AND item_id = :item_id"),
            {"card_id": card_id, "item_id": item_id}
        )
        removed = result.rowcount
        if removed > 0:
            logger.info(f"删除卡券-商品关联: card_id={card_id}, item_id={item_id}")
        return removed > 0

    async def batch_delete_relations_by_item_ids(self, item_ids: List[str]) -> int:
        """
        批量清空多个商品的所有卡券关联记录（同时清空旧字段 xy_cards.item_id）
        
        Args:
            item_ids: 商品ID列表
            
        Returns:
            删除的关联表记录总数
        """
        if not item_ids:
            return 0
        # 1. 删除关联表记录
        del_stmt = text(
            "DELETE FROM xy_card_item_relations WHERE item_id IN :item_ids"
        ).bindparams(bindparam("item_ids", expanding=True))
        result = await self.session.execute(del_stmt, {"item_ids": item_ids})
        removed = result.rowcount
        
        # 2. 清空旧字段 xy_cards.item_id（向后兼容，置为 NULL）
        upd_stmt = text(
            "UPDATE xy_cards SET item_id = NULL WHERE item_id IN :item_ids"
        ).bindparams(bindparam("item_ids", expanding=True))
        await self.session.execute(upd_stmt, {"item_ids": item_ids})
        
        await self.session.flush()
        logger.info(f"批量清空商品关联卡券: 商品数={len(item_ids)}, 删除关联记录={removed}")
        return removed

    # ==================== 内部方法 ====================

    async def _query_cards_with_source(self, item_id: str) -> List[tuple]:
        """
        从关联表查询商品关联的启用卡券，同时返回 source 和 dock_record_id。
        不使用 .scalars() 以避免 SQLAlchemy identity map 去重。
        
        Args:
            item_id: 商品ID
            
        Returns:
            [(Card, source, dock_record_id), ...] 元组列表
        """
        stmt = (
            select(Card, CardItemRelation.source, CardItemRelation.dock_record_id)
            .join(
                CardItemRelation,
                Card.id == CardItemRelation.card_id,
            )
            .where(
                CardItemRelation.item_id == item_id,
                Card.enabled == True,
            )
        )
        result = await self.session.execute(stmt)
        return list(result.all())

    async def _query_cards_from_legacy(self, item_id: str) -> List[Card]:
        """
        从 xy_cards.item_id 字段查询（向后兼容回退）
        
        Args:
            item_id: 商品ID
            
        Returns:
            Card 对象列表
        """
        stmt = select(Card).where(
            Card.item_id == item_id,
            Card.enabled == True,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def _match_cards_by_spec(
        self,
        cards: List[Card],
        spec_name: Optional[str],
        spec_value: Optional[str],
    ) -> List[Dict[str, Any]]:
        """
        根据规格信息过滤匹配的卡券
        
        匹配规则：
        - 有规格信息时：多规格卡券需 spec_name+spec_value 完全匹配
        - 无规格信息时：只返回非多规格卡券（通用卡券）
        
        Args:
            cards: Card 对象列表
            spec_name: 规格名称
            spec_value: 规格值
            
        Returns:
            匹配的卡券字典列表
        """
        matched = []
        has_spec_info = bool(spec_name and spec_value)
        
        for card in cards:
            if card.is_multi_spec:
                if has_spec_info:
                    card_sn = (card.spec_name or '').strip().lower()
                    card_sv = (card.spec_value or '').strip().lower()
                    input_sn = spec_name.strip().lower()
                    input_sv = spec_value.strip().lower()
                    
                    if card_sn == input_sn and card_sv == input_sv:
                        matched.append(self._card_to_dict(card))
                        logger.info(f"多规格卡券匹配成功: {card.name} [{spec_name}:{spec_value}]")
                    else:
                        logger.debug(
                            f"多规格卡券匹配失败: 卡券[{card.spec_name}:{card.spec_value}] "
                            f"vs 订单[{spec_name}:{spec_value}]"
                        )
                # 多规格卡券但没有传入规格信息，跳过
            else:
                # 非多规格卡券：只有在没有传入规格信息时才添加
                if not has_spec_info:
                    matched.append(self._card_to_dict(card))
        
        return matched

    def _match_card_dicts_by_spec(
        self,
        card_dicts: List[Dict[str, Any]],
        spec_name: Optional[str],
        spec_value: Optional[str],
    ) -> List[Dict[str, Any]]:
        """
        根据规格信息过滤匹配的卡券（字典版本）
        
        与 _match_cards_by_spec 逻辑相同，但操作对象是字典列表而非 Card 对象列表。
        用于关联表查询后已转为字典的场景。
        
        Args:
            card_dicts: 卡券字典列表
            spec_name: 规格名称
            spec_value: 规格值
            
        Returns:
            匹配的卡券字典列表
        """
        matched = []
        has_spec_info = bool(spec_name and spec_value)
        
        for cd in card_dicts:
            if cd.get("is_multi_spec"):
                if has_spec_info:
                    card_sn = (cd.get("spec_name") or '').strip().lower()
                    card_sv = (cd.get("spec_value") or '').strip().lower()
                    input_sn = spec_name.strip().lower()
                    input_sv = spec_value.strip().lower()
                    
                    if card_sn == input_sn and card_sv == input_sv:
                        matched.append(cd)
                        logger.info(f"多规格卡券匹配成功: {cd.get('name')} [{spec_name}:{spec_value}]")
                    else:
                        logger.debug(
                            f"多规格卡券匹配失败: 卡券[{cd.get('spec_name')}:{cd.get('spec_value')}] "
                            f"vs 订单[{spec_name}:{spec_value}]"
                        )
            else:
                if not has_spec_info:
                    matched.append(cd)
        
        return matched

    @staticmethod
    def _card_to_dict(card: Card) -> Dict[str, Any]:
        """
        将 Card 对象转换为字典
        
        Args:
            card: Card 对象
            
        Returns:
            卡券字典
        """
        # 解析 api_config JSON
        api_config = None
        if card.api_config:
            try:
                api_config = json.loads(card.api_config)
            except (json.JSONDecodeError, TypeError):
                api_config = card.api_config

        # 解析 image_urls JSON
        image_urls = None
        if card.image_urls:
            try:
                image_urls = json.loads(card.image_urls)
            except (json.JSONDecodeError, TypeError):
                image_urls = None

        return {
            "id": card.id,
            "user_id": card.user_id,
            "item_id": card.item_id,
            "name": card.name,
            "type": card.type,
            "description": card.description,
            "enabled": card.enabled,
            "delay_seconds": card.delay_seconds or 0,
            "use_no_logistics_form": bool(card.use_no_logistics_form),
            "delivery_count": card.delivery_count,
            "is_multi_spec": card.is_multi_spec or False,
            "spec_name": card.spec_name,
            "spec_value": card.spec_value,
            "api_config": api_config,
            "text_content": card.text_content,
            "data_content": card.data_content,
            "image_url": card.image_url,
            "image_urls": image_urls,
            "created_at": safe_isoformat(card.created_at),
            "updated_at": safe_isoformat(card.updated_at),
        }
