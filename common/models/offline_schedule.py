"""
自动下架规则模型

功能：
1. 定义自动下架规则表结构（xy_offline_schedules）
2. 存储下架筛选参数（上架天数阈值 X / 无订单天数 Y / 每账号下架上限 Z）
3. 关联下架账号（仅处理这些账号的商品），到时间自动筛选并批量下架
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
    offline_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7,
        comment="上架天数阈值X：上架早于X天前的商品才下架"
    )
    no_order_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="无订单天数Y：最近Y天内无订单才下架，0=不检查订单"
    )
    max_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10,
        comment="下架数量上限Z：每个账号每次触发最多下架Z个商品"
    )

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="上次触发时间"
    )
    next_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="下次触发时间（预计算，方便扫描）"
    )
