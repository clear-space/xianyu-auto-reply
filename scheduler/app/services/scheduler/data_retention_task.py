"""
数据保留清理定时任务

功能：
1. 默认每小时执行一次统一数据保留清理
2. 清理对象为日志类历史数据（详见 common/services/data_retention_service.py 的表注册表）
3. 保留天数来自 xy_system_settings 的 data_retention.* 配置，默认 30 天
4. 每次执行写 xy_data_cleanup_log 审计记录（该审计表自身也按保留天数自清理）

设计要点：
- 表名硬编码白名单，杜绝 SQL 注入
- 分批删除（默认每批 1000 行）+ 批间让出，避免长事务锁表
- 单表单轮删除上限（默认 100 批），防止首次上线历史积压过大拖慢单轮执行
- 单表失败不影响其它表
- 执行锁防并发（定时循环与手动触发互斥）
"""

from __future__ import annotations

import asyncio

from loguru import logger

from common.services.data_retention_service import run_all_cleanup


class DataRetentionTaskService:
    """数据保留清理定时任务服务"""

    def __init__(self):
        self.task_name = "数据保留清理"
        # 执行锁：避免定时循环与手动触发并发执行
        self._lock = asyncio.Lock()

    async def execute(self) -> None:
        """执行一轮数据保留清理。

        若已有清理在执行中，则跳过本次触发。
        """
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有清理正在执行，跳过本次触发")
            return
        async with self._lock:
            await run_all_cleanup()


# 模块级单例（与其它任务服务保持一致的模式）
data_retention_task_service = DataRetentionTaskService()
