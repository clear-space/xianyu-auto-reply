"""
定时发布规则模型

功能：
1. 定义定时发布规则表结构（xy_publish_schedules）
2. 存储定时发布的调度配置（单次/每天/每周 + 时间点/时间段随机）
3. 关联发布账号和素材，到时间自动触发批量发布
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class PublishSchedule(TimestampMixin, Base):
    """定时发布规则表"""

    __tablename__ = "xy_publish_schedules"
    __table_args__ = (
        Index("idx_ps_user", "user_id"),
        Index("idx_ps_next_trigger", "enabled", "next_trigger_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment="所属用户ID")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="规则名称")

    # 调度配置
    schedule_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="once",
        comment="重复模式：once-单次, daily-每天, weekly-每周"
    )
    schedule_config: Mapped[dict] = mapped_column(JSON, nullable=False, comment="时间配置JSON")

    # 发布配置
    account_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="闲鱼账号ID列表")
    material_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="素材ID列表")
    publish_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="specified",
        comment="发布模式：specified-指定发布（全部所选素材），random-随机发布"
    )
    random_count: Mapped[int | None] = mapped_column(
        Integer, comment="随机发布数量（publish_mode=random 时有效，每次触发从素材池随机选 N 条）"
    )
    deduplicate_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="去重开关（随机模式可用）：发布前刷新账号在售列表，按素材标题前缀编号（A+数字）过滤已存在"
    )

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="上次触发时间"
    )
    next_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="下次触发时间（预计算，方便扫描）"
    )
