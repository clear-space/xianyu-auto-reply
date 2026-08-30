"""
系统信息路由（管理员专用）

功能：
1. 实时系统运行状态快照（CPU/内存/磁盘/网络/进程/服务探活/告警）
2. 历史趋势数据（分钟级明细 + 小时聚合，由 scheduler 的 system_metrics_collect 任务采集）
3. 存储分布（关键目录体积 + 数据库备份清单）
4. 数据库详情（TOP 表排行 + 数据保留策略生效状态）
5. 服务与进程详情（探活、日志文件大小）
6. 告警列表与确认

数据来源：
- 实时快照：backend-web 进程本地 psutil 采集（EXE 单机部署为宿主全量视角；
  Docker 部署为容器视角，磁盘为挂载卷宿主视角）
- 历史数据：xy_system_metrics / xy_system_metrics_hourly（scheduler 每分钟采样）
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import func, select, text

from app.api import deps
from common.db.session import async_session_maker
from common.models.data_cleanup_log import DataCleanupLog
from common.models.db_backup_log import DbBackupLog
from common.models.system_metric import SystemAlert, SystemMetric, SystemMetricHourly
from common.models.user import User
from common.services.data_retention_service import get_policy_table_stats
from common.services.system_metrics import (
    collect_dir_sizes,
    collect_host_metrics,
    collect_mysql_metrics,
    collect_redis_metrics,
    invalidate_dir_size_cache,
    probe_service_health,
)
from common.utils.backup_paths import get_backup_root
from common.utils.time_utils import get_beijing_now_naive

router = APIRouter(tags=["system_info"])


def _parse_hours(raw: str, default: int = 24, maximum: int = 24 * 30) -> int:
    try:
        hours = int(str(raw).strip())
    except (ValueError, TypeError):
        return default
    return max(1, min(hours, maximum))


def _parse_json(text_value: Optional[str]) -> object:
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except (ValueError, TypeError):
        return None


@router.get("/summary")
async def get_system_summary(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """系统运行状态实时快照（含活跃告警）。"""
    host = collect_host_metrics()
    dirs = collect_dir_sizes()

    # 最近一次 scheduler 采样的 MySQL/Redis 指标（实时库连接数等信息由后端现场采集）
    latest_mysql: object = None
    latest_redis: object = None
    active_alerts: list[dict] = []
    try:
        async with async_session_maker() as session:
            latest = await session.execute(
                select(SystemMetric).order_by(SystemMetric.created_at.desc()).limit(1)
            )
            latest_row = latest.scalars().first()
            if latest_row:
                latest_mysql = _parse_json(latest_row.mysql)
                latest_redis = _parse_json(latest_row.redis)

            alerts_result = await session.execute(
                select(SystemAlert)
                .where(SystemAlert.status == "active")
                .order_by(SystemAlert.created_at.desc())
                .limit(20)
            )
            for alert in alerts_result.scalars().all():
                active_alerts.append({
                    "id": alert.id,
                    "alert_type": alert.alert_type,
                    "level": alert.level,
                    "title": alert.title,
                    "detail": _parse_json(alert.detail),
                    "created_at": alert.created_at.isoformat() if alert.created_at else None,
                })
    except Exception as exc:
        logger.error(f"[系统信息] 读取指标/告警失败: {exc}")

    # 现场探活（websocket/scheduler）
    from app.core.config import get_settings
    settings = get_settings()
    services: dict = {}
    if getattr(settings, "websocket_service_url", ""):
        services["websocket"] = await probe_service_health(settings.websocket_service_url)
    if getattr(settings, "scheduler_service_url", ""):
        services["scheduler"] = await probe_service_health(settings.scheduler_service_url)
    services["mysql"] = {"available": bool(latest_mysql and latest_mysql.get("available"))}
    services["redis"] = {"available": bool(latest_redis and latest_redis.get("available"))}

    return {
        "success": True,
        "data": {
            "host": host,
            "dirs": dirs,
            "mysql": latest_mysql,
            "redis": latest_redis,
            "services": services,
            "active_alerts": active_alerts,
            "active_alert_count": len(active_alerts),
        },
    }


@router.get("/metrics")
async def get_system_metrics(
    hours: str = "24",
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """系统指标趋势数据。

    范围 <= 2 小时读分钟明细（降采样至最多 300 点），更长范围读小时聚合表。
    """
    h = _parse_hours(hours)
    since = get_beijing_now_naive() - timedelta(hours=h)

    try:
        async with async_session_maker() as session:
            if h <= 2:
                rows = (
                    await session.execute(
                        select(
                            SystemMetric.created_at,
                            SystemMetric.cpu_percent,
                            SystemMetric.mem_percent,
                            SystemMetric.disk,
                            SystemMetric.net,
                        )
                        .where(SystemMetric.created_at >= since)
                        .order_by(SystemMetric.created_at.asc())
                    )
                ).fetchall()
                points = [
                    {
                        "ts": row[0].isoformat(),
                        "cpu": row[1],
                        "mem": row[2],
                        "disk_max_percent": _max_disk_percent(_parse_json(row[3])),
                        "net": _parse_json(row[4]),
                    }
                    for row in rows
                ]
                points = _downsample(points, 300)
            else:
                rows = (
                    await session.execute(
                        select(SystemMetricHourly)
                        .where(SystemMetricHourly.hour_start >= since.replace(minute=0, second=0, microsecond=0))
                        .order_by(SystemMetricHourly.hour_start.asc())
                    )
                ).scalars().all()
                points = [
                    {
                        "ts": row.hour_start.isoformat(),
                        "cpu": row.cpu_avg,
                        "cpu_max": row.cpu_max,
                        "mem": row.mem_avg,
                        "mem_max": row.mem_max,
                        "disk_max_percent": _max_disk_percent(_parse_json(row.disk)),
                        "net": _parse_json(row.net),
                        "sample_count": row.sample_count,
                    }
                    for row in rows
                ]
            return {"success": True, "data": {"points": points, "hours": h}}
    except Exception as exc:
        logger.error(f"[系统信息] 读取趋势数据失败: {exc}")
        return {"success": False, "message": f"读取趋势数据失败: {exc}", "data": {"points": []}}


def _max_disk_percent(disk_json: object) -> Optional[float]:
    if not isinstance(disk_json, list) or not disk_json:
        return None
    percents = [d.get("percent") for d in disk_json if isinstance(d, dict) and d.get("percent") is not None]
    return max(percents) if percents else None


def _downsample(points: list, limit: int) -> list:
    """降采样：超过 limit 点时按时间分桶取最大值（CPU/内存峰值更有告警参考价值）。"""
    if len(points) <= limit:
        return points
    bucket_size = (len(points) + limit - 1) // limit
    result = []
    for i in range(0, len(points), bucket_size):
        bucket = points[i:i + bucket_size]
        merged = dict(bucket[0])
        merged["cpu"] = max((p.get("cpu") or 0) for p in bucket)
        merged["mem"] = max((p.get("mem") or 0) for p in bucket)
        merged["disk_max_percent"] = max(
            (p.get("disk_max_percent") or 0) for p in bucket
        )
        result.append(merged)
    return result


@router.get("/storage")
async def get_system_storage(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """存储分布：关键目录体积 + 数据库备份清单。"""
    dirs = collect_dir_sizes()

    backups: list[dict] = []
    try:
        backup_root = get_backup_root()
        if backup_root.is_dir():
            for file in sorted(backup_root.glob("backup_*.sql.gz"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]:
                stat = file.stat()
                backups.append({
                    "name": file.name,
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                })
    except Exception as exc:
        logger.error(f"[系统信息] 读取备份清单失败: {exc}")

    return {"success": True, "data": {"dirs": dirs, "backups": backups}}


@router.get("/tables")
async def get_system_tables(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """数据库详情：TOP 表排行（现场查询）+ 数据保留策略生效状态。"""
    top_tables: list[dict] = []
    db_total_size = 0
    try:
        from common.core.config import get_settings
        database = get_settings().mysql_database
        async with async_session_maker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT table_name, table_rows, data_length, index_length "
                        "FROM information_schema.tables "
                        "WHERE table_schema = :db AND table_type = 'BASE TABLE' "
                        "ORDER BY (data_length + index_length) DESC LIMIT 10"
                    ),
                    {"db": database},
                )
            ).fetchall()
            top_tables = [
                {
                    "table_name": row[0],
                    "rows": int(row[1] or 0),
                    "data_length": int(row[2] or 0),
                    "index_length": int(row[3] or 0),
                }
                for row in rows
            ]
            total = (
                await session.execute(
                    text(
                        "SELECT COALESCE(SUM(data_length + index_length), 0) "
                        "FROM information_schema.tables "
                        "WHERE table_schema = :db AND table_type = 'BASE TABLE'"
                    ),
                    {"db": database},
                )
            ).fetchone()
            db_total_size = int(total[0] or 0)
    except Exception as exc:
        logger.error(f"[系统信息] 读取表排行失败: {exc}")

    policy_stats = await get_policy_table_stats()

    return {
        "success": True,
        "data": {
            "db_total_size": db_total_size,
            "top_tables": top_tables,
            "retention_policies": policy_stats,
        },
    }


@router.get("/services")
async def get_system_services(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """服务与进程详情：探活结果 + 各服务日志文件大小。"""
    from pathlib import Path

    from app.core.config import get_settings
    settings = get_settings()

    log_candidates = [
        ("backend-web", Path("logs") / "backend-web.log"),
        ("websocket", Path("..") / "websocket" / "logs" / "websocket.log"),
        ("scheduler", Path("..") / "scheduler" / "logs" / "scheduler.log"),
    ]

    service_infos: list[dict] = []
    for name, rel_path in log_candidates:
        try:
            log_path = (Path.cwd() / rel_path).resolve()
            log_size = log_path.stat().st_size if log_path.is_file() else None
        except Exception:
            log_size = None
        service_infos.append({"name": name, "log_size": log_size})

    probes: dict = {}
    if getattr(settings, "websocket_service_url", ""):
        probes["websocket"] = await probe_service_health(settings.websocket_service_url)
    if getattr(settings, "scheduler_service_url", ""):
        probes["scheduler"] = await probe_service_health(settings.scheduler_service_url)

    mysql_metrics = await collect_mysql_metrics()
    redis_metrics = await collect_redis_metrics()

    return {
        "success": True,
        "data": {
            "services": service_infos,
            "probes": probes,
            "mysql": mysql_metrics,
            "redis": redis_metrics,
        },
    }


@router.get("/alerts")
async def get_system_alerts(
    status_filter: str = "active",
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """告警列表（status_filter: active/resolved/acked/all）。"""
    try:
        async with async_session_maker() as session:
            stmt = select(SystemAlert).order_by(SystemAlert.created_at.desc()).limit(100)
            if status_filter in ("active", "resolved", "acked"):
                stmt = stmt.where(SystemAlert.status == status_filter)
            rows = (await session.execute(stmt)).scalars().all()
            alerts = [
                {
                    "id": alert.id,
                    "source": alert.source,
                    "alert_type": alert.alert_type,
                    "level": alert.level,
                    "title": alert.title,
                    "detail": _parse_json(alert.detail),
                    "status": alert.status,
                    "created_at": alert.created_at.isoformat() if alert.created_at else None,
                    "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
                }
                for alert in rows
            ]
        return {"success": True, "data": {"alerts": alerts}}
    except Exception as exc:
        logger.error(f"[系统信息] 读取告警失败: {exc}")
        return {"success": False, "message": f"读取告警失败: {exc}"}


@router.get("/cleanup-report")
async def get_cleanup_report(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """最近一批数据保留清理的审计结果（含最近一次数据库备份结果）。

    供「一键清理/单类清理」完成后展示本次清理删除了哪些表、多少行。
    批次判定：取审计表最新 created_at 前后 5 秒内的所有记录（同一轮清理写入）。
    """
    from datetime import timedelta

    batch_time: str | None = None
    tables: list[dict] = []
    total_deleted = 0
    last_backup: dict | None = None

    try:
        async with async_session_maker() as session:
            latest_ts = (
                await session.execute(select(func.max(DataCleanupLog.created_at)))
            ).scalar()
            if latest_ts is not None:
                batch_time = latest_ts.isoformat()
                window_start = latest_ts - timedelta(seconds=5)
                rows = (
                    await session.execute(
                        select(DataCleanupLog)
                        .where(DataCleanupLog.created_at >= window_start)
                        .order_by(DataCleanupLog.table_name.asc())
                    )
                ).scalars().all()
                tables = [
                    {
                        "table_name": row.table_name,
                        "deleted_rows": row.deleted_rows,
                        "remaining_rows": row.remaining_rows,
                        "duration_ms": row.duration_ms,
                        "status": row.status,
                    }
                    for row in rows
                ]
                total_deleted = sum(row.deleted_rows for row in rows)

            latest_backup_row = (
                await session.execute(
                    select(DbBackupLog).order_by(DbBackupLog.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if latest_backup_row:
                last_backup = {
                    "status": latest_backup_row.status,
                    "file_name": latest_backup_row.file_name,
                    "file_size": latest_backup_row.file_size,
                    "duration_ms": latest_backup_row.duration_ms,
                    "created_at": latest_backup_row.created_at.isoformat()
                    if latest_backup_row.created_at else None,
                }
        return {
            "success": True,
            "data": {
                "batch_time": batch_time,
                "total_deleted": total_deleted,
                "tables": tables,
                "last_backup": last_backup,
            },
        }
    except Exception as exc:
        logger.error(f"[系统信息] 读取清理审计报告失败: {exc}")
        return {
            "success": False,
            "message": f"读取清理审计报告失败: {exc}",
            "data": {"batch_time": None, "total_deleted": 0, "tables": [], "last_backup": None},
        }


@router.post("/refresh-dirs")
async def refresh_dirs(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """清空目录体积缓存并立即重新统计（清理任务执行后调用，即时反映最新体积）。"""
    invalidate_dir_size_cache()
    dirs = collect_dir_sizes()
    return {"success": True, "data": {"dirs": dirs}}


@router.post("/alerts/ack")
async def ack_system_alert(
    payload: dict,
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """确认告警（标记为 acked）。"""
    alert_id = payload.get("alert_id") or payload.get("id")
    if alert_id is None:
        return {"success": False, "message": "缺少 alert_id"}
    try:
        async with async_session_maker() as session:
            alert = await session.get(SystemAlert, int(alert_id))
            if alert is None:
                return {"success": False, "message": "告警不存在"}
            alert.status = "acked"
            await session.commit()
        return {"success": True, "message": "告警已确认"}
    except Exception as exc:
        logger.error(f"[系统信息] 确认告警失败: {exc}")
        return {"success": False, "message": f"确认告警失败: {exc}"}
