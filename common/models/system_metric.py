"""
系统运行指标模型

功能：
1. xy_system_metrics：分钟级系统运行指标明细（CPU/内存/磁盘/网络/MySQL/Redis/目录体积）
2. xy_system_metrics_hourly：小时级聚合（avg/max/min），供趋势图表长期展示
3. xy_system_alerts：系统告警事件（资源阈值/服务探活失败等）

说明：
- 指标由 scheduler 的 system_metrics_collect 任务采集写入
- 三张表均纳入统一数据保留引擎（data_retention.system_metric*_days），不会无限增长
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class SystemMetric(TimestampMixin, Base):
    """系统运行指标明细表（分钟级采样）"""

    __tablename__ = "xy_system_metrics"
    __table_args__ = (
        Index("idx_sm_source_created", "source", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    # 采集来源（主机名+服务名，用于区分多机部署）
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="", comment="采集来源")
    # CPU
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float, comment="CPU使用率(%)")
    cpu_per_core: Mapped[Optional[str]] = mapped_column(Text, comment="每核CPU使用率(JSON数组)")
    # 内存（字节）
    mem_total: Mapped[Optional[float]] = mapped_column(Float, comment="内存总量(字节)")
    mem_used: Mapped[Optional[float]] = mapped_column(Float, comment="内存已用(字节)")
    mem_available: Mapped[Optional[float]] = mapped_column(Float, comment="内存可用(字节)")
    mem_percent: Mapped[Optional[float]] = mapped_column(Float, comment="内存使用率(%)")
    process_rss: Mapped[Optional[float]] = mapped_column(Float, comment="采集进程RSS(字节)")
    # 系统负载与进程
    load_avg: Mapped[Optional[str]] = mapped_column(Text, comment="系统负载(JSON)")
    process_count: Mapped[Optional[int]] = mapped_column(Integer, comment="系统进程数")
    # 磁盘（每挂载点）
    disk: Mapped[Optional[str]] = mapped_column(Text, comment="磁盘使用(JSON)")
    # 网络
    net: Mapped[Optional[str]] = mapped_column(Text, comment="网络IO(JSON)")
    # 关键目录体积
    dirs: Mapped[Optional[str]] = mapped_column(Text, comment="关键目录体积(JSON)")
    # 数据库与缓存
    mysql: Mapped[Optional[str]] = mapped_column(Text, comment="MySQL指标(JSON)")
    redis: Mapped[Optional[str]] = mapped_column(Text, comment="Redis指标(JSON)")


class SystemMetricHourly(TimestampMixin, Base):
    """系统运行指标小时聚合表（趋势图表数据源）"""

    __tablename__ = "xy_system_metrics_hourly"
    __table_args__ = (
        Index("idx_smh_source_hour", "source", "hour_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="", comment="采集来源")
    # 聚合窗口起始时间（整点，北京时间）
    hour_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True, comment="聚合窗口起始时间")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="样本数")
    cpu_avg: Mapped[Optional[float]] = mapped_column(Float, comment="CPU平均(%)")
    cpu_max: Mapped[Optional[float]] = mapped_column(Float, comment="CPU峰值(%)")
    mem_avg: Mapped[Optional[float]] = mapped_column(Float, comment="内存平均(%)")
    mem_max: Mapped[Optional[float]] = mapped_column(Float, comment="内存峰值(%)")
    disk: Mapped[Optional[str]] = mapped_column(Text, comment="磁盘聚合(JSON)")
    net: Mapped[Optional[str]] = mapped_column(Text, comment="网络聚合(JSON)")
    mysql: Mapped[Optional[str]] = mapped_column(Text, comment="MySQL聚合(JSON)")
    redis: Mapped[Optional[str]] = mapped_column(Text, comment="Redis聚合(JSON)")


class SystemAlert(TimestampMixin, Base):
    """系统告警事件表"""

    __tablename__ = "xy_system_alerts"
    __table_args__ = (
        Index("idx_sa_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="", comment="告警来源")
    # 告警类型：cpu/mem/disk/service/mysql/redis/backup
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True, comment="告警类型")
    # 级别：warning/critical
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="warning", comment="级别：warning/critical")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="告警标题")
    # 触发时的详情（JSON）
    detail: Mapped[Optional[str]] = mapped_column(Text, comment="告警详情(JSON)")
    # 状态：active（未恢复）/ resolved（已恢复）/ acked（已确认）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", comment="状态：active/resolved/acked")
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="恢复时间")
