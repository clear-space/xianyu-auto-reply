"""
数据保留清理审计日志模型

功能：
1. 定义数据保留清理审计表结构（xy_data_cleanup_log）
2. 记录统一数据保留引擎每次执行对每张表的清理结果（表名、删除行数、耗时、状态）
3. 供管理员在系统信息看板/审计中验证数据治理策略是否生效
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class DataCleanupLog(TimestampMixin, Base):
    """数据保留清理审计日志表"""

    __tablename__ = "xy_data_cleanup_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    # 被清理的表名（硬编码白名单内的表，绝不接受外部输入）
    table_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="被清理的表名")
    # 本次清理删除的行数
    deleted_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="本次删除行数")
    # 清理后剩余行数（取样值，用于评估表增长趋势）
    remaining_rows: Mapped[int | None] = mapped_column(BigInteger, comment="清理后剩余行数(取样)")
    # 该表清理耗时（毫秒）
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="该表清理耗时(毫秒)")
    # 清理状态：success（成功）/ failed（失败）/ skipped（配置禁用或未到清理条件）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success", comment="状态：success/failed/skipped")
    # 失败时的错误信息
    error_message: Mapped[str | None] = mapped_column(String(1000), comment="错误信息")
