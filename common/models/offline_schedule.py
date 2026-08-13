"""
自动下架规则模型

功能：
1. 按「上架天数 > X」和「最近 Y 天无订单」筛选商品
2. 按上架天数倒序取前 Z 个商品批量下架
3. 支持每天/每周定时自动执行
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, JSON, Integer, String, text
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

    # 下架参数
    age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, comment="已上架天数阈值 X")
    no_order_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, comment="最近N天无订单 Y")
    offline_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5, comment="下架数量上限 Z")

    # 调度配置
    schedule_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="daily",
        comment="重复模式：daily-每天, weekly-每周"
    )
    schedule_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, comment="时间配置JSON")

    # 账号
    account_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="闲鱼账号ID列表")

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="上次触发时间"
    )
    next_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="下次触发时间"
    )
