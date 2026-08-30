"""
统一数据保留清理服务

功能：
1. 集中清理各日志类表的历史数据，解决「只写不清」导致的无限增长问题
2. 保留天数全部来自 xy_system_settings 的 data_retention.* 配置，默认 30 天
3. 分批删除（LIMIT 批大小 + 批间短暂让出），每批独立提交，避免长事务锁表
4. 单表失败不影响其它表；每次执行写 xy_data_cleanup_log 审计记录
5. 同时提供单表清理辅助函数，供各定时任务内嵌的 _cleanup_expired_logs 复用，
   统一保留天数来源（不再各自硬编码 10 天）

安全设计：
- 表名全部来自模块内硬编码白名单注册表，绝不接受任何外部输入（杜绝 SQL 注入）
- 只删除超期数据（时间列 < 截止时间），不触碰任何未到期行
- 配置缺失/无效时回退默认值；data_retention.enabled=false 时整体跳过
- 审计日志表自身也在注册表中，按保留天数自清理，不会无限增长
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.session import async_session_maker
from common.models.data_cleanup_log import DataCleanupLog
from common.models.system_setting import SystemSetting
from common.utils.time_utils import get_beijing_now_naive

# ============ 默认值（配置缺失或无效时回退） ============

# 日志类数据默认保留天数
DEFAULT_RETENTION_DAYS = 30

# token_cache 软过期行物理删除的缓冲天数：expire_at 与 renew_expire_at 均已过期
# 超过该天数后才物理删除（避免误删「即将被续期流程重新写入」的行）
DEFAULT_TOKEN_CACHE_BUFFER_DAYS = 7

# 审计日志自身保留天数
DEFAULT_CLEANUP_LOG_DAYS = 30

# 单批删除行数
DEFAULT_BATCH_SIZE = 1000

# 单表单轮删除的最大批次数（批大小 × 上限 = 单轮最大删除行数，
# 防止首次上线时历史积压过大导致单轮执行时间过长）
DEFAULT_MAX_BATCHES_PER_TABLE = 100

# 批间让出时长（秒），避免持续占用数据库连接与锁
BATCH_SLEEP_SECONDS = 0.2

# ============ 配置键 ============

CONFIG_ENABLED = "data_retention.enabled"
CONFIG_BATCH_SIZE = "data_retention.cleanup_batch_size"
CONFIG_MAX_BATCHES = "data_retention.max_batches_per_table"
CONFIG_SCHEDULED_TASK_LOG_DAYS = "data_retention.scheduled_task_log_days"
CONFIG_CLEANUP_LOG_DAYS = "data_retention.cleanup_log_days"

# ============ 表注册表（硬编码白名单） ============
# 条目结构：(表名, 时间列, 保留天数配置键, 清理模式)
# 清理模式：created_at=按时间列批量删除；token_cache=软过期缓冲删除
# 注意：xy_goofish_crawl_items 的时间列为 fetched_at（该表无 created_at）

_CLEANUP_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("xy_scheduled_token_renewal_log", "created_at", "data_retention.token_renewal_log_days", "created_at"),
    ("xy_scheduled_cookies_refresh_log", "created_at", "data_retention.cookies_refresh_log_days", "created_at"),
    ("xy_auto_reply_message_logs", "created_at", "data_retention.auto_reply_message_log_days", "created_at"),
    ("xy_default_reply_records", "created_at", "data_retention.default_reply_record_days", "created_at"),
    ("xy_account_login_logs", "created_at", "data_retention.account_login_log_days", "created_at"),
    ("xy_publish_logs", "created_at", "data_retention.publish_log_days", "created_at"),
    ("xy_risk_control_logs", "created_at", "data_retention.risk_control_log_days", "created_at"),
    ("xy_ai_chat_messages", "created_at", "data_retention.ai_chat_message_days", "created_at"),
    ("xy_goofish_crawl_items", "fetched_at", "data_retention.goofish_crawl_item_days", "created_at"),
    ("xy_scheduled_close_notice_log", "created_at", CONFIG_SCHEDULED_TASK_LOG_DAYS, "created_at"),
    ("xy_token_cache", "expire_at", "data_retention.token_cache_soft_expired_days", "token_cache"),
    # 审计日志自身也纳入清理，避免其无限增长
    ("xy_data_cleanup_log", "created_at", CONFIG_CLEANUP_LOG_DAYS, "created_at"),
    # 系统信息看板的指标表（分钟明细/小时聚合/告警事件），同样纳入保留清理
    ("xy_system_metrics", "created_at", "data_retention.system_metric_days", "created_at"),
    ("xy_system_metrics_hourly", "created_at", "data_retention.system_metric_hourly_days", "created_at"),
    ("xy_system_alerts", "created_at", "data_retention.system_alert_days", "created_at"),
)


async def get_setting_int(key: str, default: int, minimum: int = 1, maximum: int = 3650) -> int:
    """从 xy_system_settings 读取整数配置。

    每次执行时现读（不缓存），管理员修改后下次执行即生效。
    值无效或越界时回退默认值。
    """
    try:
        async with async_session_maker() as session:
            stmt = select(SystemSetting.value).where(SystemSetting.key == key)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                value = int(str(row).strip())
                if minimum <= value <= maximum:
                    return value
    except Exception as exc:
        logger.warning(f"[数据保留清理] 读取配置 {key} 失败，使用默认值 {default}: {exc}")
    return default


async def get_retention_days(config_key: str, default: int = DEFAULT_RETENTION_DAYS) -> int:
    """读取某张表对应的保留天数配置。"""
    return await get_setting_int(config_key, default)


async def cleanup_created_at_table(
    session: AsyncSession,
    table_name: str,
    days_config_key: str,
    log_prefix: str = "[数据保留清理]",
) -> int:
    """单表清理辅助函数：删除时间列早于 (当前北京时间 - 保留天数) 的记录。

    供统一清理引擎与各定时任务内嵌的 _cleanup_expired_logs 共用，
    统一保留天数来源。返回删除行数；失败时抛出异常由调用方处理。
    """
    days = await get_retention_days(days_config_key)
    cutoff = get_beijing_now_naive() - timedelta(days=days)
    batch_size = await get_setting_int(CONFIG_BATCH_SIZE, DEFAULT_BATCH_SIZE)
    max_batches = await get_setting_int(CONFIG_MAX_BATCHES, DEFAULT_MAX_BATCHES_PER_TABLE)
    deleted = await _delete_in_batches(
        session, table_name, "created_at", cutoff, batch_size, max_batches, log_prefix
    )
    if deleted > 0:
        logger.info(f"{log_prefix} 表 {table_name} 已清理 {deleted} 条超过 {days} 天的记录（截止: {cutoff}）")
    return deleted


async def _delete_in_batches(
    session: AsyncSession,
    table_name: str,
    column: str,
    cutoff,
    batch_size: int,
    max_batches: int,
    log_prefix: str,
    extra_condition: str = "",
) -> int:
    """分批执行 DELETE，每批独立提交，批间让出事件循环。

    table_name/column 仅来自模块内白名单注册表，不接受外部输入。
    """
    total = 0
    condition = f"`{column}` < :cutoff"
    if extra_condition:
        condition += f" AND {extra_condition}"
    sql = text(f"DELETE FROM `{table_name}` WHERE {condition} LIMIT :batch_size")
    for _ in range(max_batches):
        result = await session.execute(
            sql, {"cutoff": cutoff, "batch_size": batch_size}
        )
        await session.commit()
        deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < batch_size:
            break
        await asyncio.sleep(BATCH_SLEEP_SECONDS)
    return total


async def _cleanup_token_cache_table(
    session: AsyncSession, log_prefix: str = "[数据保留清理]"
) -> int:
    """清理 xy_token_cache 中「已软过期且超过缓冲天数」的行。

    条件：expire_at 早于截止时间，且 renew_expire_at 为空或也早于截止时间。
    （renew_expire_at 仍有效说明续期流程可能还会写回该行，不删除）
    """
    buffer_days = await get_setting_int(
        "data_retention.token_cache_soft_expired_days", DEFAULT_TOKEN_CACHE_BUFFER_DAYS
    )
    cutoff = get_beijing_now_naive() - timedelta(days=buffer_days)
    batch_size = await get_setting_int(CONFIG_BATCH_SIZE, DEFAULT_BATCH_SIZE)
    max_batches = await get_setting_int(CONFIG_MAX_BATCHES, DEFAULT_MAX_BATCHES_PER_TABLE)
    deleted = await _delete_in_batches(
        session,
        "xy_token_cache",
        "expire_at",
        cutoff,
        batch_size,
        max_batches,
        log_prefix,
        extra_condition="(`renew_expire_at` IS NULL OR `renew_expire_at` < :cutoff)",
    )
    if deleted > 0:
        logger.info(
            f"{log_prefix} 表 xy_token_cache 已物理删除 {deleted} 条软过期超过 {buffer_days} 天的记录"
        )
    return deleted


async def _sample_remaining_rows(session: AsyncSession, table_name: str) -> Optional[int]:
    """取样统计某表当前总行数（用于审计记录，失败返回 None 不影响主流程）。"""
    try:
        result = await session.execute(text(f"SELECT COUNT(*) FROM `{table_name}`"))
        return int(result.scalar() or 0)
    except Exception:
        return None


async def run_all_cleanup() -> list[dict]:
    """执行一轮全表保留清理，并写入审计日志。

    返回本轮清理结果列表（每表一条 dict）。单表失败不影响其它表。
    """
    enabled = await get_setting_int(CONFIG_ENABLED, 1, minimum=0, maximum=1)
    if not enabled:
        logger.info("[数据保留清理] data_retention.enabled=false，本轮跳过")
        return []

    results: list[dict] = []
    logger.info("[数据保留清理] 开始执行统一数据保留清理")

    async with async_session_maker() as session:
        for table_name, column, config_key, mode in _CLEANUP_TABLES:
            record = {
                "table_name": table_name,
                "deleted_rows": 0,
                "status": "success",
                "error_message": None,
                "remaining_rows": None,
                "duration_ms": 0,
            }
            start_ms = time.monotonic()
            try:
                if mode == "token_cache":
                    deleted = await _cleanup_token_cache_table(session)
                else:
                    deleted = await cleanup_created_at_table(session, table_name, config_key)
                record["deleted_rows"] = deleted
            except Exception as exc:
                record["status"] = "failed"
                record["error_message"] = str(exc)[:1000]
                logger.error(f"[数据保留清理] 清理表 {table_name} 失败: {exc}")
                try:
                    await session.rollback()
                except Exception:
                    pass
            finally:
                record["duration_ms"] = int((time.monotonic() - start_ms) * 1000)

            if record["status"] == "success":
                record["remaining_rows"] = await _sample_remaining_rows(session, table_name)
            results.append(record)

        # 写审计日志（放在同一会话末尾，避免逐表频繁提交）
        try:
            for record in results:
                session.add(
                    DataCleanupLog(
                        table_name=record["table_name"],
                        deleted_rows=record["deleted_rows"],
                        remaining_rows=record["remaining_rows"],
                        duration_ms=record["duration_ms"],
                        status=record["status"],
                        error_message=record["error_message"],
                    )
                )
            await session.commit()
        except Exception as exc:
            logger.error(f"[数据保留清理] 写审计日志失败: {exc}")
            try:
                await session.rollback()
            except Exception:
                pass

    total_deleted = sum(r["deleted_rows"] for r in results)
    logger.info(f"[数据保留清理] 本轮完成，共清理 {total_deleted} 行，涉及 {len(results)} 张表")
    return results


async def get_policy_table_stats() -> list[dict]:
    """统计各保留策略表的当前状态（行数、最旧/最新记录、配置保留天数）。

    供系统信息看板的「数据保留策略生效状态」区块展示。
    任何单表统计失败仅跳过该表，不影响整体。
    """
    results: list[dict] = []
    for table_name, column, config_key, _mode in _CLEANUP_TABLES:
        days = await get_retention_days(config_key)
        stats: dict = {
            "table_name": table_name,
            "config_key": config_key,
            "retention_days": days,
            "rows": None,
            "oldest": None,
            "newest": None,
        }
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    text(
                        f"SELECT COUNT(*), MIN(`{column}`), MAX(`{column}`) "
                        f"FROM `{table_name}`"
                    )
                )
                row = result.fetchone()
                if row:
                    stats["rows"] = int(row[0] or 0)
                    stats["oldest"] = row[1].isoformat() if row[1] else None
                    stats["newest"] = row[2].isoformat() if row[2] else None
        except Exception:
            pass
        results.append(stats)
    return results


class DataRetentionService:
    """统一数据保留清理任务服务（供 scheduler 定时任务与手动触发使用）。"""

    def __init__(self):
        self.task_name = "数据保留清理"
        # 执行锁：避免定时循环与手动触发并发执行
        self._lock = asyncio.Lock()

    async def execute(self) -> None:
        """执行一轮数据保留清理。

        若已有清理在执行中，则跳过本次触发（避免并发重复清理）。
        """
        if self._lock.locked():
            logger.warning(f"【{self.task_name}】已有清理正在执行，跳过本次触发")
            return
        async with self._lock:
            await run_all_cleanup()
