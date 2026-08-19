"""
上架权重算法模型

功能：
1. 定义上架权重算法表（xy_weight_algorithms）
2. 管理员集中定义权重调参规则，定时发布规则引用算法ID来获得素材权重
3. 参数说明：
   - first_use_bonus: 首次发布加成（从未使用过的素材）
   - recent_order_bonus: 近30天有订单加成
   - sold_bonus: 已售出加成
   - offline_recover_per_day: 自动下架后每天恢复分数
   - deleted_recover_per_day: 手动删除后每天恢复分数（更慢）
   - fail_penalty: 近60天单次发布失败扣分
   - exclude_sold: 已售出是否硬排除（True=不参与随机）
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class WeightAlgorithm(TimestampMixin, Base):
    """上架权重算法表"""

    __tablename__ = "xy_weight_algorithms"
    __table_args__ = (
        Index("idx_wa_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="算法名称")
    algorithm_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="heat_weight",
        comment="算法类型：heat_weight-热度加权（后续可扩展其他类型）",
    )
    description: Mapped[str | None] = mapped_column(String(500), comment="算法说明")
    params: Mapped[dict] = mapped_column(JSON, nullable=False, comment="权重参数JSON")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="是否系统内置（内置算法仅硬排已售出可调，不可删除/停用，列表置顶）",
    )
