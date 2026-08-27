"""
自动下架规则模型

功能：
1. 定义自动下架规则表结构（xy_offline_schedules）
2. 存储下架参数（每账号下架上限 Z / 下架权重算法引用），到时间自动按权重选品并批量下架
3. 关联下架账号（仅处理这些账号的商品）
4. 时间配置复用定时发布模块（common.utils.schedule_time 统一计算 next_trigger_at）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class OfflineSchedule(TimestampMixin, Base):
    """自动下架规则表"""

    __tablename__ = "xy_offline_schedules"
    __table_args__ = (
        Index("idx_os_user", "user_id"),
        Index("idx_os_next_trigger", "enabled", "next_trigger_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment="所属用户ID")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="规则名称")

    # 调度配置（复用模块一：每天/每周 + 固定时间点/时间段随机）
    schedule_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="daily",
        comment="重复模式：daily-每天, weekly-每周"
    )
    schedule_config: Mapped[dict] = mapped_column(JSON, nullable=False, comment="时间配置JSON")

    # 下架参数
    account_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="闲鱼账号ID列表（仅下架这些账号的商品）")
    max_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="下架数量上限Z：每个账号每次触发最多下架Z个商品"
    )
    delist_algorithm_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="下架权重算法ID（选品排序；NULL=系统默认参数）",
    )

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="上次触发时间"
    )
    next_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="下次触发时间（预计算，方便扫描）"
    )
