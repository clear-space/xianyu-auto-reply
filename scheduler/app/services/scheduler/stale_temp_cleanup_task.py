"""
过期临时文件清理定时任务

功能：
1. 默认每小时执行一次，清理进程异常退出等场景遗留的临时文件/目录：
   - browser_data/slider_sessions/ 下超过 24 小时的滑块会话临时 profile（正常关闭会自清，仅残留需要清扫）
   - 系统临时目录 %TEMP%/xianyu_publish_images 下超过 24 小时的发布图片残留
   - backups/_uploads/ 下超过 7 天的恢复上传残留文件
2. 只删除「超过保留期」的文件/目录，不触碰任何新文件

设计要点：
- 按 mtime 判定超期；目录（slider_sessions 子目录）整目录删除
- 任何删除失败仅记录日志，不影响其它条目
- 无数据库写入（纯文件系统清扫，无审计表）
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

from loguru import logger

from common.utils.backup_paths import get_backup_root
from common.utils.data_paths import get_browser_data_root

# 滑块会话临时 profile 保留期（小时）
SLIDER_SESSIONS_RETENTION_HOURS = 24
# 发布临时图片保留期（小时）
PUBLISH_IMAGES_RETENTION_HOURS = 24
# 恢复上传残留保留期（天）
RESTORE_UPLOADS_RETENTION_DAYS = 7


class StaleTempCleanupTaskService:
    """过期临时文件清理定时任务服务"""

    def __init__(self):
        self.task_name = "过期临时文件清理"
        # 执行锁：避免定时循环与手动触发并发执行
        self._lock = asyncio.Lock()

    async def execute(self) -> None:
        """执行一轮过期临时文件清理。

        若已有清理在执行中，则跳过本次触发。
        """
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有清理正在执行，跳过本次触发")
            return
        async with self._lock:
            await self._run_cleanup()

    def _clean_dir_children(self, target_dir: Path, retention_seconds: int, label: str) -> dict:
        """删除目录下超过保留期的直接子项（子目录整目录删除，文件直接删除）。"""
        stats = {"deleted": 0, "kept": 0, "failed": 0}
        if not target_dir.is_dir():
            return stats
        now = time.time()
        try:
            entries = list(target_dir.iterdir())
        except Exception as e:
            logger.error(f"【{self.task_name}】{label}目录扫描失败: {e}")
            return stats
        for entry in entries:
            try:
                if now - entry.stat().st_mtime < retention_seconds:
                    stats["kept"] += 1
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                stats["deleted"] += 1
                logger.info(f"【{self.task_name}】✓ 已删除{label}过期条目: {entry.name}")
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"【{self.task_name}】✗ 删除{label}条目失败: {entry}, 错误: {e}")
        return stats

    async def _run_cleanup(self) -> None:
        """实际执行一轮清理。"""
        logger.info(f"【{self.task_name}】开始执行")
        start_time = time.time()
        total_deleted = 0

        # 1. 滑块会话临时 profile（browser_data/slider_sessions/ 下超过 24 小时的子目录）
        try:
            slider_sessions_dir = get_browser_data_root() / "slider_sessions"
            stats = self._clean_dir_children(
                slider_sessions_dir,
                SLIDER_SESSIONS_RETENTION_HOURS * 3600,
                "滑块会话临时目录",
            )
            total_deleted += stats["deleted"]
            logger.info(
                f"【{self.task_name}】滑块会话临时目录：删除 {stats['deleted']}，"
                f"未到期保留 {stats['kept']}，失败 {stats['failed']}"
            )
        except Exception as exc:
            logger.error(f"【{self.task_name}】清理滑块会话临时目录异常: {exc}")

        # 2. 发布临时图片（%TEMP%/xianyu_publish_images 下超过 24 小时的文件）
        try:
            publish_images_dir = Path(tempfile.gettempdir()) / "xianyu_publish_images"
            stats = self._clean_dir_children(
                publish_images_dir,
                PUBLISH_IMAGES_RETENTION_HOURS * 3600,
                "发布临时图片",
            )
            total_deleted += stats["deleted"]
            logger.info(
                f"【{self.task_name}】发布临时图片目录：删除 {stats['deleted']}，"
                f"未到期保留 {stats['kept']}，失败 {stats['failed']}"
            )
        except Exception as exc:
            logger.error(f"【{self.task_name}】清理发布临时图片目录异常: {exc}")

        # 3. 恢复上传残留（backups/_uploads/ 下超过 7 天的文件）
        try:
            restore_uploads_dir = get_backup_root() / "_uploads"
            stats = self._clean_dir_children(
                restore_uploads_dir,
                RESTORE_UPLOADS_RETENTION_DAYS * 86400,
                "恢复上传残留",
            )
            total_deleted += stats["deleted"]
            logger.info(
                f"【{self.task_name}】恢复上传残留目录：删除 {stats['deleted']}，"
                f"未到期保留 {stats['kept']}，失败 {stats['failed']}"
            )
        except Exception as exc:
            logger.error(f"【{self.task_name}】清理恢复上传残留目录异常: {exc}")

        elapsed = time.time() - start_time
        logger.info(
            f"【{self.task_name}】清理完成，共删除 {total_deleted} 个过期条目，耗时 {elapsed:.2f}秒"
        )


# 创建全局实例
stale_temp_cleanup_task_service = StaleTempCleanupTaskService()
