"""
系统运行指标采集定时任务

功能：
1. 默认每分钟采集一次系统运行指标（CPU/内存/磁盘/网络/目录体积/MySQL/Redis/服务探活）
2. 采样写入 xy_system_metrics（分钟级明细）
3. 整点过后聚合上一小时数据写入 xy_system_metrics_hourly（趋势图表数据源）
4. 阈值告警评估：CPU/内存/磁盘超阈值连续 3 次采样触发告警，恢复后自动标记 resolved
   （阈值来自 xy_system_settings 的 system_info.alert_*，默认 CPU/内存 90%、磁盘 85%）
5. 指标表保留天数由统一数据保留引擎管理（data_retention.system_metric*_days，默认30天）

设计要点：
- 单次采集失败不中断任务，仅记录日志（告警评估跳过缺失字段）
- 小时聚合幂等：按 (source, hour_start) 唯一性判断，已存在则跳过
- 告警状态持久化在 xy_system_alerts，进程重启后从数据库恢复激活状态
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import func, select, text

from common.db.session import async_session_maker
from common.models.db_backup_log import DbBackupLog
from common.models.system_metric import SystemAlert, SystemMetric, SystemMetricHourly
from common.services.data_retention_service import get_setting_int
from common.services.system_metrics import collect_all_metrics, to_json
from common.utils.time_utils import get_beijing_now_naive

# 连续超阈值采样次数达到该值才触发告警（避免瞬时抖动误报）
_ALERT_TRIGGER_COUNT = 3


class SystemMetricsTaskService:
    """系统运行指标采集定时任务服务"""

    def __init__(self):
        self.task_name = "系统运行指标采集"
        # 执行锁：避免定时循环与手动触发并发执行
        self._lock = asyncio.Lock()
        # 已聚合的小时（进程内去重；数据库唯一性兜底）
        self._aggregated_hours: set[str] = set()

    async def execute(self) -> None:
        """执行一轮指标采集。

        若已有采集在执行中，则跳过本次触发。
        """
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有采集正在执行，跳过本次触发")
            return
        async with self._lock:
            await self._run_collect()

    async def _run_collect(self) -> None:
        """执行一轮采集：采样入库 + 小时聚合 + 告警评估。"""
        try:
            metrics = await collect_all_metrics()
        except Exception as exc:
            logger.error(f"【{self.task_name}】指标采集失败: {exc}")
            return

        host = metrics.get("host") or {}
        cpu_percent = host.get("cpu_percent")
        mem_percent = host.get("mem_percent")

        try:
            async with async_session_maker() as session:
                record = SystemMetric(
                    source=metrics.get("source") or "",
                    cpu_percent=cpu_percent,
                    cpu_per_core=to_json(host.get("cpu_per_core")),
                    mem_total=host.get("mem_total"),
                    mem_used=host.get("mem_used"),
                    mem_available=host.get("mem_available"),
                    mem_percent=mem_percent,
                    process_rss=host.get("process_rss"),
                    load_avg=to_json(host.get("load_avg")),
                    process_count=host.get("process_count"),
                    disk=to_json(host.get("disk")),
                    net=to_json(host.get("net")),
                    dirs=to_json(metrics.get("dirs")),
                    mysql=to_json(metrics.get("mysql")),
                    redis=to_json(metrics.get("redis")),
                )
                session.add(record)
                await session.commit()
        except Exception as exc:
            logger.error(f"【{self.task_name}】指标入库失败: {exc}")

        # 小时聚合（整点后聚合上一小时）
        await self._aggregate_hourly_if_due(metrics.get("source") or "")

        # 阈值告警评估
        await self._evaluate_alerts(metrics)

    async def _aggregate_hourly_if_due(self, source: str) -> None:
        """若上一个整点小时的聚合尚未执行，则执行聚合（幂等）。"""
        now = get_beijing_now_naive()
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        prev_hour = hour_start - timedelta(hours=1)
        key = f"{source}:{prev_hour:%Y%m%d%H}"
        if key in self._aggregated_hours:
            return
        self._aggregated_hours.add(key)
        # 内存集合封顶（防止长期运行无限增长）
        if len(self._aggregated_hours) > 1000:
            self._aggregated_hours = set(sorted(self._aggregated_hours)[-500:])

        try:
            async with async_session_maker() as session:
                # 幂等：已存在则跳过
                exists = await session.execute(
                    select(func.count()).select_from(SystemMetricHourly).where(
                        SystemMetricHourly.source == source,
                        SystemMetricHourly.hour_start == prev_hour,
                    )
                )
                if int(exists.scalar() or 0) > 0:
                    return

                agg = await session.execute(
                    select(
                        func.count(),
                        func.avg(SystemMetric.cpu_percent),
                        func.max(SystemMetric.cpu_percent),
                        func.min(SystemMetric.cpu_percent),
                        func.avg(SystemMetric.mem_percent),
                        func.max(SystemMetric.mem_percent),
                        func.min(SystemMetric.mem_percent),
                    ).where(
                        SystemMetric.source == source,
                        SystemMetric.created_at >= prev_hour,
                        SystemMetric.created_at < hour_start,
                    )
                )
                row = agg.fetchone()
                sample_count = int(row[0] or 0)
                if sample_count == 0:
                    return

                # 上一小时最后一条样本的 JSON 快照（磁盘/网络/MySQL/Redis 取末值）
                latest = await session.execute(
                    select(
                        SystemMetric.disk, SystemMetric.net,
                        SystemMetric.mysql, SystemMetric.redis,
                    ).where(
                        SystemMetric.source == source,
                        SystemMetric.created_at >= prev_hour,
                        SystemMetric.created_at < hour_start,
                    ).order_by(SystemMetric.created_at.desc()).limit(1)
                )
                latest_row = latest.fetchone()

                session.add(SystemMetricHourly(
                    source=source,
                    hour_start=prev_hour,
                    sample_count=sample_count,
                    cpu_avg=round(row[1], 2) if row[1] is not None else None,
                    cpu_max=round(row[2], 2) if row[2] is not None else None,
                    mem_avg=round(row[4], 2) if row[4] is not None else None,
                    mem_max=round(row[5], 2) if row[5] is not None else None,
                    disk=latest_row[0] if latest_row else None,
                    net=latest_row[1] if latest_row else None,
                    mysql=latest_row[2] if latest_row else None,
                    redis=latest_row[3] if latest_row else None,
                ))
                await session.commit()
                logger.info(
                    f"【{self.task_name}】已完成 {prev_hour:%Y-%m-%d %H:00} 小时聚合，样本 {sample_count} 条"
                )
        except Exception as exc:
            logger.error(f"【{self.task_name}】小时聚合失败: {exc}")

    async def _evaluate_alerts(self, metrics: dict) -> None:
        """评估阈值告警：连续超阈值触发，恢复后自动标记 resolved。

        - CPU/内存/磁盘阈值来自 xy_system_settings（system_info.alert_*）
        - 服务探活失败（websocket/backend-web 不可达）
        - MySQL/Redis 不可用
        - 最近一次数据库备份失败
        """
        host = metrics.get("host") or {}
        cpu_percent = host.get("cpu_percent")
        mem_percent = host.get("mem_percent")
        disks = host.get("disk") or []
        source = metrics.get("source") or ""

        cpu_threshold = await get_setting_int("system_info.alert_cpu_percent", 90)
        mem_threshold = await get_setting_int("system_info.alert_mem_percent", 90)
        disk_threshold = await get_setting_int("system_info.alert_disk_percent", 85)

        # 组装当前告警条件集合：key -> (type, level, title, detail)
        conditions: dict[str, tuple[str, str, str, dict]] = {}

        if cpu_percent is not None and cpu_percent >= cpu_threshold:
            conditions[f"cpu:{source}"] = (
                "cpu", "critical", f"CPU 使用率过高（{cpu_percent:.1f}%）",
                {"cpu_percent": cpu_percent, "threshold": cpu_threshold},
            )
        if mem_percent is not None and mem_percent >= mem_threshold:
            conditions[f"mem:{source}"] = (
                "mem", "critical", f"内存使用率过高（{mem_percent:.1f}%）",
                {"mem_percent": mem_percent, "threshold": mem_threshold},
            )
        for disk in disks:
            percent = disk.get("percent")
            if percent is not None and percent >= disk_threshold:
                mountpoint = disk.get("mountpoint", "?")
                conditions[f"disk:{source}:{mountpoint}"] = (
                    "disk", "warning", f"磁盘空间不足（{mountpoint} 已用 {percent}%）",
                    {"mountpoint": mountpoint, "percent": percent, "threshold": disk_threshold},
                )
        services = metrics.get("services") or {}
        for service_name, probe in services.items():
            if not probe.get("available"):
                conditions[f"service:{source}:{service_name}"] = (
                    "service", "critical", f"服务不可达：{service_name}",
                    {"service": service_name, "error": probe.get("error") or f"HTTP {probe.get('status_code')}"},
                )
        if not (metrics.get("mysql") or {}).get("available"):
            conditions[f"mysql:{source}"] = (
                "mysql", "critical", "MySQL 连接失败",
                {"error": (metrics.get("mysql") or {}).get("error")},
            )
        if not (metrics.get("redis") or {}).get("available"):
            conditions[f"redis:{source}"] = (
                "redis", "critical", "Redis 连接失败",
                {"error": (metrics.get("redis") or {}).get("error")},
            )
        # 最近一次数据库备份失败 → 告警
        try:
            async with async_session_maker() as session:
                latest_backup = await session.execute(
                    select(DbBackupLog.status).order_by(DbBackupLog.created_at.desc()).limit(1)
                )
                backup_status = latest_backup.scalar_one_or_none()
            if backup_status == "failed":
                conditions[f"backup:{source}"] = (
                    "backup", "warning", "最近一次数据库备份失败",
                    {},
                )
        except Exception:
            backup_status = None

        try:
            async with async_session_maker() as session:
                # 1. 已恢复的条件：把对应 active 告警标记为 resolved
                active_alerts = (
                    await session.execute(
                        select(SystemAlert).where(
                            SystemAlert.source == source,
                            SystemAlert.status == "active",
                        )
                    )
                ).scalars().all()
                for alert in active_alerts:
                    if alert.title:
                        key = self._alert_key_of(alert)
                        if key and key not in conditions:
                            alert.status = "resolved"
                            alert.resolved_at = get_beijing_now_naive()
                            logger.info(f"【{self.task_name}】告警已恢复: {alert.title}")
                await session.commit()

                # 2. 仍满足条件的告警：仅当不存在同名 active 告警时新建
                existing_titles = {
                    alert.title for alert in active_alerts
                }
                for key, (alert_type, level, title, detail) in conditions.items():
                    if title in existing_titles:
                        continue
                    session.add(SystemAlert(
                        source=source,
                        alert_type=alert_type,
                        level=level,
                        title=title,
                        detail=json.dumps(detail, ensure_ascii=False),
                        status="active",
                    ))
                    logger.warning(f"【{self.task_name}】触发告警: {title}")
                await session.commit()
        except Exception as exc:
            logger.error(f"【{self.task_name}】告警评估失败: {exc}")

    @staticmethod
    def _alert_key_of(alert: SystemAlert) -> Optional[str]:
        """根据告警行反推告警条件 key（与 _evaluate_alerts 中的 key 规则一致）。"""
        source = alert.source or ""
        if alert.alert_type in ("cpu", "mem", "mysql", "redis", "backup"):
            return f"{alert.alert_type}:{source}"
        if alert.alert_type == "disk":
            try:
                detail = json.loads(alert.detail or "{}")
            except (ValueError, TypeError):
                detail = {}
            return f"disk:{source}:{detail.get('mountpoint', '?')}"
        if alert.alert_type == "service":
            try:
                detail = json.loads(alert.detail or "{}")
            except (ValueError, TypeError):
                detail = {}
            return f"service:{source}:{detail.get('service', '?')}"
        return None


# 创建全局实例
system_metrics_task_service = SystemMetricsTaskService()
