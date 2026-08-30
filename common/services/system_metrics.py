"""
系统运行指标采集器

功能：
1. 主机指标（CPU/内存/磁盘/网络/负载/进程数）—— psutil
2. 关键目录体积统计（60 秒短 TTL 缓存，避免大目录频繁全量扫描）
3. MySQL 指标（库总体积、TOP 表、连接数、慢查询）—— information_schema + SHOW GLOBAL STATUS
4. Redis 指标（内存、命中率、连接数）—— INFO
5. 服务探活（websocket/scheduler 的 /health/ping）

输出统一的 dict 结构，供 scheduler 的 system_metrics_collect 任务与
backend-web 的 /admin/system-info 实时接口共用。

Docker 语义说明：
- 容器内 psutil 的 CPU/内存为容器视角（cgroup 配额），磁盘为挂载卷宿主视角
- EXE/源码部署为完整宿主视角
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional

import psutil
from loguru import logger

from common.core.config import get_settings
from common.db.redis_client import get_redis_client

# 目录体积缓存 TTL（秒）：大目录频繁全量扫描开销大，采集间隔 60 秒时缓存足够新
_DIR_SIZE_CACHE_TTL_SECONDS = 60

# 默认监控的关键目录（键为展示名，值为目录 Path）
_DEFAULT_DIRS: Dict[str, Path] = {}

# 目录体积缓存：path -> (ts, size_bytes, file_count)
_dir_size_cache: Dict[str, tuple[float, int, int]] = {}

# 网络累计计数缓存：用于计算两次采样间的速率（首轮无速率返回 0）
_net_cache: Dict[str, tuple[float, int, int]] = {}


def _hostname() -> str:
    return socket.gethostname()


def collect_host_metrics() -> dict:
    """采集主机运行指标（同步，实时）。"""
    result: Dict[str, Any] = {
        "hostname": _hostname(),
        "boot_time": None,
        "uptime_seconds": None,
        "cpu_percent": None,
        "cpu_per_core": None,
        "cpu_count": psutil.cpu_count() or 0,
        "mem_total": None,
        "mem_used": None,
        "mem_available": None,
        "mem_percent": None,
        "process_rss": None,
        "load_avg": None,
        "process_count": None,
        "disk": [],
        "net": None,
        "platform": f"{os.name}/{_sys_platform()}",
    }
    try:
        # CPU（非阻塞采样，返回自上次调用以来的平均；首次调用可能为 0）
        result["cpu_percent"] = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        result["cpu_per_core"] = [round(v, 1) for v in per_core]
    except Exception as exc:
        logger.warning(f"[系统指标] CPU 采集失败: {exc}")

    try:
        vm = psutil.virtual_memory()
        result["mem_total"] = vm.total
        result["mem_used"] = vm.used
        result["mem_available"] = vm.available
        result["mem_percent"] = round(vm.percent, 1)
    except Exception as exc:
        logger.warning(f"[系统指标] 内存采集失败: {exc}")

    try:
        result["boot_time"] = psutil.boot_time()
        result["uptime_seconds"] = int(time.time() - psutil.boot_time())
        result["process_count"] = len(psutil.pids())
        result["process_rss"] = psutil.Process(os.getpid()).memory_info().rss
    except Exception as exc:
        logger.warning(f"[系统指标] 进程/启动时间采集失败: {exc}")

    try:
        if hasattr(psutil, "getloadavg"):
            result["load_avg"] = [round(v, 2) for v in psutil.getloadavg()]
    except Exception:
        pass

    result["disk"] = _collect_disks()
    result["net"] = _collect_net()
    return result


def _sys_platform() -> str:
    try:
        import sys
        return sys.platform
    except Exception:
        return "unknown"


def _disk_usage_safe(mountpoint: str):
    """安全获取磁盘使用情况。

    psutil 5.9.0 在 Windows 上遇非 ASCII 卷标时 disk_usage 会抛
    "argument 1 (impossible<bad format char>)"（5.9.3 已修复）；
    此类异常回退到标准库 shutil.disk_usage（不依赖 psutil）。
    """
    import shutil

    try:
        return psutil.disk_usage(mountpoint)
    except Exception:
        try:
            return shutil.disk_usage(mountpoint)
        except Exception:
            return None


def _disk_usage_percent(usage) -> float:
    """计算磁盘使用百分比。

    psutil 的 sdiskusage 自带 percent；shutil.disk_usage 返回的 namedtuple 无该字段，
    需要手工计算。
    """
    percent = getattr(usage, "percent", None)
    if percent is not None:
        return round(float(percent), 1)
    total = getattr(usage, "total", 0)
    if total:
        return round(getattr(usage, "used", 0) / total * 100, 1)
    return 0.0


def _collect_disks() -> list:
    """采集各挂载点磁盘使用（过滤虚拟/不可用挂载点）。"""
    disks = []
    try:
        for part in psutil.disk_partitions(all=False):
            usage = _disk_usage_safe(part.mountpoint)
            if usage is None:
                continue
            disks.append({
                "mountpoint": part.mountpoint,
                "device": part.device,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": _disk_usage_percent(usage),
            })
        if disks:
            return disks
    except Exception as exc:
        logger.warning(f"[系统指标] 磁盘采集失败（disk_partitions）: {exc}")
    # 回退：psutil 5.9.0 在 Windows 上遇非 ASCII 卷标时 disk_partitions 会抛错，
    # 改为逐盘符探测（仅 Windows）
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            mountpoint = f"{letter}:\\"
            try:
                if not os.path.exists(mountpoint):
                    continue
            except OSError:
                continue
            usage = _disk_usage_safe(mountpoint)
            if usage is None:
                continue
            disks.append({
                "mountpoint": mountpoint,
                "device": mountpoint,
                "fstype": "",
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": _disk_usage_percent(usage),
            })
    return disks


def _collect_net() -> dict:
    """采集网络累计 IO，并计算两次采样间的速率（字节/秒）。"""
    now = time.time()
    try:
        counters = psutil.net_io_counters()
        sent, recv = counters.bytes_sent, counters.bytes_recv
    except Exception:
        return {"sent_rate": 0, "recv_rate": 0, "sent_total": 0, "recv_total": 0}

    key = f"{_hostname()}:net"
    prev = _net_cache.get(key)
    _net_cache[key] = (now, sent, recv)
    sent_rate = recv_rate = 0
    if prev is not None:
        prev_ts, prev_sent, prev_recv = prev
        delta = max(now - prev_ts, 0.0001)
        sent_rate = int(max(sent - prev_sent, 0) / delta)
        recv_rate = int(max(recv - prev_recv, 0) / delta)
    return {
        "sent_rate": sent_rate,
        "recv_rate": recv_rate,
        "sent_total": sent,
        "recv_total": recv,
    }


def get_default_monitor_dirs() -> Dict[str, Path]:
    """默认监控的关键目录（首次调用时解析，此后复用）。

    目录解析规则与备份路径/浏览器数据路径保持一致：
    - STATIC_DIR 环境变量（Docker 共享卷）
    - BACKUP_DIR 环境变量
    - 根日志目录经 get_logs_root() 解析（DATA_ROOT 感知，Docker 挂载 logs-data 卷）、
      各服务日志目录位于项目根 backend-web/websocket/scheduler/logs

    兼容 Docker 部署：
    - 各服务日志目录（backend-web/websocket/scheduler/logs）仅在目录存在时登记，
      容器内未挂载的目录自动跳过（统计结果 0 不代表真实为空，而是不可见）
    - 浏览器数据目录与根日志目录均经 DATA_ROOT 解析（Docker 共享卷挂载点）
    """
    global _DEFAULT_DIRS
    if _DEFAULT_DIRS:
        return _DEFAULT_DIRS

    from common.utils.backup_paths import get_backup_root
    from common.utils.data_paths import get_browser_data_root, get_logs_root, get_project_root

    dirs: Dict[str, Path] = {}
    static_env = os.environ.get("STATIC_DIR", "")
    if static_env:
        static_root = Path(static_env)
        if not static_root.is_absolute():
            static_root = Path(os.getcwd()) / static_root
    else:
        static_root = get_project_root() / "backend-web" / "static"
    dirs["上传文件"] = static_root / "uploads"

    try:
        dirs["数据库备份"] = get_backup_root()
    except Exception:
        pass

    dirs["浏览器数据"] = get_browser_data_root()

    # 根日志目录（launcher 日志、滑块轨迹历史等）——存在才登记：
    # 与写入方共用 get_logs_root()（DATA_ROOT 感知）；Docker 下挂载 logs-data 卷，
    # scheduler 采集快照时可见 backend-web 写入的轨迹历史，本地与服务器视图一致。
    # 目录不存在（源码/EXE 尚未产生根日志）时不登记，避免显示一行永远为 0 的「日志」
    root_logs = get_logs_root()
    if root_logs.is_dir():
        dirs["日志"] = root_logs
    # 各服务日志目录（存在才登记，兼容 Docker 容器内未挂载其它服务日志卷的情况）
    for service in ("backend-web", "websocket", "scheduler"):
        service_logs = get_project_root() / service / "logs"
        if service_logs.is_dir():
            dirs[f"服务日志({service})"] = service_logs

    _DEFAULT_DIRS = dirs
    return dirs


def collect_dir_sizes(dirs: Optional[Dict[str, Path]] = None) -> Dict[str, dict]:
    """统计目录体积与文件数（60 秒短 TTL 缓存）。

    Args:
        dirs: 目录名 -> Path；未传时使用默认监控目录

    Returns:
        {"目录名": {"path": ..., "size_bytes": ..., "file_count": ...}}
    """
    targets = dirs or get_default_monitor_dirs()
    result: Dict[str, dict] = {}
    now = time.time()
    for name, path in targets.items():
        key = str(path)
        cached = _dir_size_cache.get(key)
        if cached and now - cached[0] < _DIR_SIZE_CACHE_TTL_SECONDS:
            result[name] = {"path": key, "size_bytes": cached[1], "file_count": cached[2]}
            continue
        size_bytes = 0
        file_count = 0
        try:
            if path.is_dir():
                for root, _, files in os.walk(path):
                    for file_name in files:
                        try:
                            size_bytes += (Path(root) / file_name).stat().st_size
                            file_count += 1
                        except OSError:
                            continue
        except Exception as exc:
            logger.warning(f"[系统指标] 目录统计失败 {path}: {exc}")
        _dir_size_cache[key] = (now, size_bytes, file_count)
        result[name] = {"path": key, "size_bytes": size_bytes, "file_count": file_count}
    return result


async def collect_mysql_metrics() -> dict:
    """采集 MySQL 运行指标（异步，失败返回 available=False）。"""
    result: Dict[str, Any] = {"available": False, "error": None}
    try:
        settings = get_settings()
        database = settings.mysql_database
        from common.db.session import async_session_maker
        from sqlalchemy import text

        async with async_session_maker() as session:
            # 库总体积 + TOP 表（数据+索引）
            stmt = text(
                "SELECT table_name, table_rows, data_length, index_length "
                "FROM information_schema.tables "
                "WHERE table_schema = :db AND table_type = 'BASE TABLE' "
                "ORDER BY (data_length + index_length) DESC LIMIT 10"
            )
            rows = (await session.execute(stmt, {"db": database})).fetchall()
            top_tables = [
                {
                    "table_name": row[0],
                    "rows": int(row[1] or 0),
                    "data_length": int(row[2] or 0),
                    "index_length": int(row[3] or 0),
                }
                for row in rows
            ]

            total_stmt = text(
                "SELECT COALESCE(SUM(data_length + index_length), 0), COUNT(*) "
                "FROM information_schema.tables "
                "WHERE table_schema = :db AND table_type = 'BASE TABLE'"
            )
            total_size, table_count = (
                await session.execute(total_stmt, {"db": database})
            ).fetchone()

            # 全局状态
            status_rows = (
                await session.execute(
                    text("SHOW GLOBAL STATUS WHERE Variable_name IN "
                         "('Threads_connected','Threads_running','Slow_queries','Uptime')")
                )
            ).fetchall()
            status = {row[0]: row[1] for row in status_rows}

            max_conn_rows = (
                await session.execute(text("SHOW VARIABLES LIKE 'max_connections'"))
            ).fetchall()
            max_connections = int(max_conn_rows[0][1]) if max_conn_rows else 0

        result.update({
            "available": True,
            "database": database,
            "total_size": int(total_size or 0),
            "table_count": int(table_count or 0),
            "top_tables": top_tables,
            "threads_connected": int(status.get("Threads_connected") or 0),
            "threads_running": int(status.get("Threads_running") or 0),
            "slow_queries": int(status.get("Slow_queries") or 0),
            "uptime_seconds": int(status.get("Uptime") or 0),
            "max_connections": max_connections,
        })
    except Exception as exc:
        result["error"] = str(exc)[:500]
        logger.warning(f"[系统指标] MySQL 指标采集失败: {exc}")
    return result


async def collect_redis_metrics() -> dict:
    """采集 Redis 运行指标（失败返回 available=False）。"""
    result: Dict[str, Any] = {"available": False, "error": None}
    try:
        # get_redis_client 为异步函数（redis.asyncio），必须先 await
        client = await get_redis_client()
        info = await client.info()
        keyspace_hits = int(info.get("keyspace_hits") or 0)
        keyspace_misses = int(info.get("keyspace_misses") or 0)
        total_lookups = keyspace_hits + keyspace_misses
        result.update({
            "available": True,
            "used_memory": int(info.get("used_memory") or 0),
            "used_memory_peak": int(info.get("used_memory_peak") or 0),
            "connected_clients": int(info.get("connected_clients") or 0),
            "keyspace_hits": keyspace_hits,
            "keyspace_misses": keyspace_misses,
            "hit_rate": round(keyspace_hits / total_lookups * 100, 2) if total_lookups else None,
            "uptime_seconds": int(info.get("uptime_in_seconds") or 0),
            "redis_version": info.get("redis_version", ""),
        })
    except Exception as exc:
        result["error"] = str(exc)[:500]
        logger.warning(f"[系统指标] Redis 指标采集失败: {exc}")
    return result


async def probe_service_health(url: str) -> dict:
    """探活内部服务健康接口（3 秒超时）。

    各服务健康路径约定不一致：backend-web 为 /health/ping，
    websocket/scheduler 为 /health —— 依次尝试两个路径，任一返回 200 即视为在线。
    注意：import 必须放在 try 内——scheduler 镜像依赖表中曾缺少 httpx，
    放在 try 外会让整轮指标采集因一个探活依赖缺失而全部失败。
    """
    try:
        import httpx
    except ImportError:
        return {"available": False, "status_code": None,
                "error": "httpx 未安装（请在服务依赖中加入 httpx）"}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            base = url.rstrip("/")
            for path in ("/health/ping", "/health"):
                try:
                    resp = await client.get(f"{base}{path}")
                except Exception:
                    continue
                if resp.status_code == 200:
                    return {
                        "available": True,
                        "status_code": 200,
                        "path": path,
                        "latency_ms": int(resp.elapsed.total_seconds() * 1000),
                    }
            return {"available": False, "status_code": None,
                    "error": "健康接口无 200 响应"}
    except Exception as exc:
        return {"available": False, "status_code": None, "error": str(exc)[:200]}


async def collect_service_probes() -> Dict[str, dict]:
    """探活 websocket 与 scheduler 服务（scheduler 采集时探活 websocket + backend-web）。"""
    probes: Dict[str, dict] = {}
    try:
        settings = get_settings()
    except Exception:
        return probes

    targets: Dict[str, str] = {}
    websocket_url = getattr(settings, "websocket_service_url", "")
    backend_url = getattr(settings, "backend_web_service_url", "")
    if websocket_url:
        targets["websocket"] = websocket_url
    if backend_url:
        targets["backend-web"] = backend_url

    for name, url in targets.items():
        probes[name] = await probe_service_health(url)
    return probes


async def collect_all_metrics() -> dict:
    """采集全量指标（供 scheduler 任务使用）。"""
    host = collect_host_metrics()
    dirs = collect_dir_sizes()
    mysql = await collect_mysql_metrics()
    redis = await collect_redis_metrics()
    probes = await collect_service_probes()
    return {
        "source": host["hostname"],
        "host": host,
        "dirs": dirs,
        "mysql": mysql,
        "redis": redis,
        "services": probes,
    }


def invalidate_dir_size_cache() -> None:
    """清空目录体积缓存。

    手动清理任务执行后调用，使下一次 collect_dir_sizes 立即重新统计，
    存储分布数字即时反映清理结果（否则要等 60 秒缓存过期）。
    """
    _dir_size_cache.clear()


def _json_default(value: Any) -> Any:
    """JSON 序列化兜底（datetime 等）。"""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def to_json(value: Any) -> str:
    """安全序列化为 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, default=_json_default)
