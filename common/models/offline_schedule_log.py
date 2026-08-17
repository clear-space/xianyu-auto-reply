"""
自动下架执行记录模型

功能：
1. 定义自动下架执行记录表（xy_offline_schedule_logs）
2. 记录每次下架规则触发后的筛选与下架结果
3. 保存规则名快照（规则删除后仍可查看名称）与商品明细
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
        Index("idx_osl_batch", "batch_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="关联的下架规则ID"
    )
    schedule_name: Mapped[str | None] = mapped_column(
        String(100), comment="规则名称快照（规则删除后执行记录仍可查看名称）"
    )
    batch_id: Mapped[str | None] = mapped_column(
        String(36), comment="批次ID（本次触发标识）"
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="计划执行时间"
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="实际执行时间"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        comment="状态：pending-待执行, running-执行中, completed-已完成, failed-失败, cancelled-已取消"
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0, comment="筛选出的商品总数（符合条件下架候选数）")
    success_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功下架数")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, comment="失败数")
    error_message: Mapped[str | None] = mapped_column(
        String(1000), comment="失败原因"
    )
    detail_json: Mapped[dict | None] = mapped_column(
        JSON, comment="执行明细JSON（每账号成功/失败商品明细、账号级错误）"
    )
