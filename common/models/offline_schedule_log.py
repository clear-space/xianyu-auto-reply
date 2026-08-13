"""
自动下架执行记录模型

功能：
1. 定义自动下架执行记录表（xy_offline_schedule_logs）
2. 记录每次下架规则触发后下架的商品明细
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class OfflineScheduleLog(TimestampMixin, Base):
    """自动下架执行记录表"""

    __tablename__ = "xy_offline_schedule_logs"
    __table_args__ = (
        Index("idx_osl_schedule", "schedule_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="关联的下架规则ID"
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="实际执行时间"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed",
        comment="状态：completed-已完成, failed-失败"
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0, comment="筛选出的商品总数")
    offlined_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功下架数量")
    offlined_items: Mapped[list | None] = mapped_column(
        JSON, comment="成功下架的商品编号列表"
    )
    error_message: Mapped[str | None] = mapped_column(
        String(1000), comment="失败原因"
    )
