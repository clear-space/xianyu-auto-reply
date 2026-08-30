"""
数据目录路径统一解析工具

背景：
browser_data、滑块轨迹历史等目录此前使用相对 CWD 路径（os.getcwd()），
不同启动方式（源码运行 / EXE 打包 / Docker）下实际落点不一致，导致
调度器清理任务与写入方可能指向不同目录。本模块统一为「基于项目根的绝对路径」，
并支持环境变量覆盖：

- DATA_ROOT：数据根目录。Docker 中设为 /app，使 browser_data 落在共享卷
  /app/browser_data（与 docker-compose 挂载保持一致）。
- browser_data 根目录：
  1. 设置了 DATA_ROOT → DATA_ROOT/browser_data
  2. 未设置 → 项目根/websocket/browser_data
     （与 EXE/源码部署的历史位置一致，避免迁移导致既有登录态丢失）
- 根日志目录（launcher 日志、滑块轨迹历史等）：
  1. 设置了 DATA_ROOT → DATA_ROOT/logs（Docker 挂载 logs-data 卷持久化）
  2. 未设置 → 项目根/logs
- 轨迹历史目录：根日志目录/trajectory_history
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根目录：common/utils/data_paths.py -> common/utils -> common -> 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_project_root() -> Path:
    """获取项目根目录（基于本模块物理位置，与 cwd 无关）。"""
    return _PROJECT_ROOT


def get_data_root() -> Path | None:
    """获取数据根目录（环境变量 DATA_ROOT）；未设置返回 None。

    相对路径值基于项目根解析，与 cwd 无关。
    """
    value = os.environ.get("DATA_ROOT", "").strip()
    if not value:
        return None
    root = Path(value)
    if not root.is_absolute():
        root = _PROJECT_ROOT / root
    return root


def get_browser_data_root() -> Path:
    """获取浏览器数据根目录（Playwright 持久化 profile 所在目录）。

    写入方（slider_stealth / drissionpage_slider / cookie_renew_browser_service /
    real_mouse_slider）与清理方（scheduler 的 cleanup_browser_data_task /
    cleanup_unconfigured_browser_data_task）必须共用此解析结果。
    """
    data_root = get_data_root()
    if data_root is not None:
        return data_root / "browser_data"
    return _PROJECT_ROOT / "websocket" / "browser_data"


def get_logs_root() -> Path:
    """获取根日志目录（launcher 日志、滑块轨迹历史等）。

    写入方（history_manager / strategy_stats 等）与读取方（存储分布「日志」行）
    必须共用此解析结果，否则 Docker 下会因容器间路径不一致导致统计缺失。
    """
    data_root = get_data_root()
    if data_root is not None:
        return data_root / "logs"
    return _PROJECT_ROOT / "logs"


def get_trajectory_history_dir() -> Path:
    """获取滑块轨迹历史/策略统计目录。"""
    return get_logs_root() / "trajectory_history"
