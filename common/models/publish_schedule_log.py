"""
定时发布执行记录模型

功能：
1. 定义定时发布执行记录表（xy_publish_schedule_logs）
2. 记录每次定时规则触发后的批量发布结果
3. 关联 batch_id 可追溯到具体发布日志
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class PublishScheduleLog(TimestampMixin, Base):
    """定时发布执行记录表"""

    __tablename__ = "xy_publish_schedule_logs"
    __table_args__ = (
        Index("idx_psl_schedule", "schedule_id"),
        Index("idx_psl_batch", "batch_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="关联的定时规则ID"
    )
    batch_id: Mapped[str | None] = mapped_column(
        String(36), comment="批量发布的 batch_id"
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
    total_count: Mapped[int] = mapped_column(Integer, default=0, comment="总发布次数（账号数×素材数）")
    success_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功次数")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, comment="失败次数")
    error_message: Mapped[str | None] = mapped_column(
        String(1000), comment="失败原因"
    )
    detail_json: Mapped[dict | None] = mapped_column(
        JSON, comment="执行详情：{success_items:[编号], failed_items:[{title,reason}]}"
    )
