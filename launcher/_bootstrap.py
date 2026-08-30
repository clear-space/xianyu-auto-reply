"""
启动器核心逻辑

所有业务逻辑均在此文件中实现，main.py 仅作为最小入口桩。

功能：
1. 自检并初始化配置文件
2. 设置 Playwright 浏览器路径
3. 根据命令行参数决定运行模式（GUI / 子服务）
"""
import os
import sys
from pathlib import Path


def main():
    """
    启动器主函数
    
    根据命令行参数决定运行模式：
    - 无参数：启动GUI界面
    - --run-service <name>：启动指定的子服务（供打包后子进程调用）
    """
    # 自检并初始化配置文件
    from launcher.config_init import init_config_files
    init_config_files()
    from launcher.browser_setup import ensure_playwright_browser_path
    ensure_playwright_browser_path()

    # 启动时清扫更新临时目录的过期残留（下载取消/中断/更新失败遗留）
    try:
        from launcher.updater import cleanup_stale_update_residue
        cleanup_stale_update_residue()
    except Exception:
        pass

    # 尝试通过环境变量触发子服务模式，避免某些打包场景下argv丢失
    service_env = os.environ.get("XR_RUN_SERVICE", "").strip()

    # 记录一次启动日志，便于诊断启动模式（超过5MB轮转一份，最多保留2份，防止无限增长）
    try:
        from launcher.frozen_detect import get_project_root
        log_dir = get_project_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        launcher_log = log_dir / "launcher.log"
        max_size = 5 * 1024 * 1024
        if launcher_log.exists() and launcher_log.stat().st_size > max_size:
            backup_log = log_dir / "launcher.log.1"
            backup_log.unlink(missing_ok=True)
            launcher_log.replace(backup_log)
        with launcher_log.open("a", encoding="utf-8") as f:
            f.write(f"args={sys.argv} env_service={service_env}\n")
    except Exception:
        pass

    if service_env:
        # 环境变量指定的子服务模式
        from launcher.service_runner import run_service
        run_service(service_env)
        return

    if len(sys.argv) >= 3 and sys.argv[1] == "--run-service":
        # 子服务运行模式（命令行参数）
        service_name = sys.argv[2]
        from launcher.service_runner import run_service
        run_service(service_name)
        return

    # GUI启动模式
    from launcher.gui import LauncherApp
    app = LauncherApp()
    app.run()
