"""
运维预警工具

功能：
1. 服务启动失败（MySQL / Redis 依赖不可用）时，通过系统通知渠道推送告警
2. 故障恢复后（服务完整启动成功）推送恢复通知并清除告警状态
3. 同类告警按时间窗口去重（默认 30 分钟），避免崩溃循环期间消息轰炸
4. 提供带指数退避的数据库连接检查，供各服务启动期依赖探测复用

设计约束：
- 所有数据库 / 渠道操作失败时仅记录日志，绝不抛出异常影响主流程
- 去重状态存于 xy_system_settings（键 ops_alert.last.* / ops_alert.active.*），
  不新增数据表；键内包含设备名与服务名，多设备独立去重
- 通知渠道复用系统通知渠道（xy_notification_channels，管理界面配置），
  与调度器自愈监测告警走同一发送路径
"""
from __future__ import annotations

import asyncio
import socket
import time
from datetime import datetime
from typing import Optional

from loguru import logger

# 数据库连接检查：单次尝试超时（秒），驱动层另有 db_connect_timeout 兜底
_DB_PROBE_TIMEOUT = 15.0
# 数据库连接检查：逐次退避延迟（秒），总窗口约 2.5 分钟
_DB_RETRY_DELAYS = (5.0, 10.0, 20.0, 40.0, 80.0)
# 通知渠道批量发送总超时（秒）
_SEND_BATCH_TIMEOUT = 60.0

# 启动失败原因 -> 告警文案
_FAILURE_LABELS = {
    "mysql": "MySQL 连接失败",
    "redis": "Redis 连接失败",
}


def _get_identity() -> tuple[str, str]:
    """返回 (设备名, 服务短名)。

    设备名优先取 DEPLOY_DEVICE_NAME（多设备部署时建议在 .env 配置，
    如 LYServer-C005），缺省回退到主机名（Docker 下为容器名）。
    服务短名由 project_name 推导（Xianyu Scheduler Service -> Scheduler）。
    """
    from common.core.config import get_settings

    settings = get_settings()
    device = (settings.deploy_device_name or "").strip() or socket.gethostname()
    # project_name 定义在各服务配置子类，使用 getattr 兜底
    short = (
        (getattr(settings, "project_name", "") or "")
        .replace("Xianyu ", "")
        .replace(" Service", "")
        .strip()
        or "Unknown"
    )
    return device, short


async def _read_setting(key: str) -> Optional[str]:
    """读取 xy_system_settings 中的字符串值；无记录返回 None"""
    from sqlalchemy import text

    from common.db.session import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT value FROM xy_system_settings WHERE `key` = :key LIMIT 1"),
            {"key": key},
        )
        row = result.fetchone()
        if not row or row[0] in (None, ""):
            return None
        return str(row[0]).strip()


async def _write_setting(key: str, value: str) -> None:
    """写入（覆盖）xy_system_settings 中的字符串值。

    MySQL 8.0.20+ 弃用 VALUES(col)，使用行别名语法（AS new）。
    """
    from sqlalchemy import text

    from common.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO xy_system_settings (`key`, value) "
                "VALUES (:key, :value) AS new "
                "ON DUPLICATE KEY UPDATE value = new.value"
            ),
            {"key": key, "value": value},
        )
        await session.commit()


async def _delete_setting(key: str) -> None:
    """删除 xy_system_settings 中的一条记录"""
    from sqlalchemy import text

    from common.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(
            text("DELETE FROM xy_system_settings WHERE `key` = :key"),
            {"key": key},
        )
        await session.commit()


async def _check_dedup(dedup_key: str) -> bool:
    """检查告警去重窗口；允许发送时记录 last 与 active 状态并返回 True。

    dedup_key 已包含设备名与服务名，多设备互不干扰。
    """
    from common.core.config import get_settings

    settings = get_settings()
    last_raw = await _read_setting(f"ops_alert.last.{dedup_key}")
    now = time.time()
    if last_raw:
        try:
            if now - float(last_raw) < settings.ops_alert_dedup_seconds:
                return False
        except ValueError:
            pass
    await _write_setting(f"ops_alert.last.{dedup_key}", str(int(now)))
    await _write_setting(f"ops_alert.active.{dedup_key}", "1")
    return True


async def _dispatch_to_channels(content: str) -> int:
    """通过全部启用中的系统通知渠道发送；返回成功发送的渠道数。

    渠道加载或单个渠道发送失败仅记录日志，不影响其它渠道。
    """
    from sqlalchemy import select

    from common.db.session import async_session_maker
    from common.models.notification_channel import NotificationChannel
    from common.utils.notification_utils import (
        parse_notification_config,
        send_bark_notification,
        send_dingtalk_notification,
        send_email_notification,
        send_feishu_notification,
        send_pushplus_notification,
        send_telegram_notification,
        send_webhook_notification,
        send_wechat_notification,
    )

    channels = []
    try:
        async with async_session_maker() as session:
            channels = list(
                (
                    await session.execute(
                        select(NotificationChannel).where(
                            NotificationChannel.enabled == True
                        )
                    )
                )
                .scalars()
                .all()
            )
    except Exception as exc:
        logger.warning(f"加载通知渠道失败，运维预警仅记录日志: {exc}")
        return 0

    if not channels:
        logger.info("未配置启用中的通知渠道，运维预警仅记录日志")
        return 0

    sent = 0
    for channel in channels:
        channel_type = channel.channel_type or ""
        try:
            config_data = parse_notification_config(channel.config_payload or {})
            if channel_type in ("dingtalk", "ding_talk"):
                ok = await send_dingtalk_notification(config_data, content)
            elif channel_type in ("feishu", "lark"):
                ok = await send_feishu_notification(config_data, content)
            elif channel_type == "bark":
                ok = await send_bark_notification(config_data, content)
            elif channel_type == "email":
                ok = await send_email_notification(config_data, content)
            elif channel_type == "webhook":
                ok = await send_webhook_notification(config_data, content)
            elif channel_type in ("wechat", "wechat_work"):
                ok = await send_wechat_notification(config_data, content)
            elif channel_type == "telegram":
                ok = await send_telegram_notification(config_data, content)
            elif channel_type == "pushplus":
                ok = await send_pushplus_notification(config_data, content)
            else:
                logger.warning(f"运维预警不支持的通知渠道类型: {channel_type}")
                continue
            if ok:
                sent += 1
        except Exception as exc:
            logger.error(f"渠道 {channel_type} 发送运维预警失败: {exc}")

    return sent


async def send_ops_failure_alert(failure_type: str, detail: str) -> None:
    """推送服务启动失败告警（按去重窗口限流）。

    Args:
        failure_type: "mysql" 或 "redis"
        detail: 失败详情（连接地址、重试次数、异常信息等）

    任何内部异常均被吞掉并记录日志，绝不影响调用方（启动失败退出路径）。
    """
    try:
        from common.core.config import get_settings

        settings = get_settings()
        if not settings.ops_alert_enabled:
            return

        device, short = _get_identity()
        dedup_key = f"startup:{failure_type}:{device}:{short.lower()}"
        if not await _check_dedup(dedup_key):
            logger.info(f"运维预警去重窗口内，跳过发送: {dedup_key}")
            return

        cause = _FAILURE_LABELS.get(failure_type, failure_type)
        content = (
            "🚨 服务启动失败告警\n"
            f"设备：{device}\n"
            f"服务：{short}\n"
            f"原因：{cause}\n"
            f"详情：{detail[:300]}\n"
            f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "说明：服务即将退出并由 Docker 自动重启；如持续告警请检查依赖服务。"
        )
        sent = 0
        async with asyncio.timeout(_SEND_BATCH_TIMEOUT):
            sent = await _dispatch_to_channels(content)
        logger.warning(f"运维预警已推送（成功渠道数={sent}）: {dedup_key}")
    except Exception as exc:
        logger.warning(f"发送运维预警失败（不影响主流程）: {exc}")


async def send_ops_recovery_alert() -> None:
    """服务完整启动成功后调用：若此前发过启动失败告警，推送恢复通知并清除状态。"""
    try:
        from common.core.config import get_settings

        settings = get_settings()
        if not settings.ops_alert_enabled:
            return

        device, short = _get_identity()
        recovered: list[str] = []
        for failure_type in ("mysql", "redis"):
            dedup_key = f"startup:{failure_type}:{device}:{short.lower()}"
            if await _read_setting(f"ops_alert.active.{dedup_key}") == "1":
                recovered.append((failure_type, dedup_key))

        if not recovered:
            return

        content = (
            "✅ 服务已恢复正常\n"
            f"设备：{device}\n"
            f"服务：{short}\n"
            f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        sent = 0
        async with asyncio.timeout(_SEND_BATCH_TIMEOUT):
            sent = await _dispatch_to_channels(content)

        # 清除 active 与 last 状态：恢复后再次故障可立即告警，不受上次窗口限制
        for _failure_type, dedup_key in recovered:
            await _delete_setting(f"ops_alert.active.{dedup_key}")
            await _delete_setting(f"ops_alert.last.{dedup_key}")
        logger.info(f"运维恢复通知已推送（成功渠道数={sent}）")
    except Exception as exc:
        logger.warning(f"发送运维恢复通知失败（不影响主流程）: {exc}")


async def check_database_with_backoff() -> bool:
    """启动期数据库连接检查（带指数退避，总窗口约 2.5 分钟）。

    最终失败时自动推送运维预警并返回 False（由调用方决定退出服务）。
    仅供服务启动流程使用；健康检查接口请保持轻量探测，勿调用本函数。
    """
    from sqlalchemy import text

    from common.core.config import get_settings
    from common.db.session import async_engine

    settings = get_settings()
    db_addr = f"{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"

    for index, delay in enumerate(_DB_RETRY_DELAYS, start=1):
        try:
            async with asyncio.timeout(_DB_PROBE_TIMEOUT):
                async with async_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            logger.info("数据库连接成功")
            return True
        except Exception as exc:
            logger.warning(
                f"数据库连接失败（第 {index}/{len(_DB_RETRY_DELAYS)} 次）: "
                f"数据库地址 {db_addr}, "
                f"error_type={type(exc).__name__}, error={exc}"
            )
            if index < len(_DB_RETRY_DELAYS):
                await asyncio.sleep(delay)

    logger.error("数据库连接失败，已达到最大重试次数")
    await send_ops_failure_alert(
        "mysql",
        f"连接 {db_addr} 失败（已重试 {len(_DB_RETRY_DELAYS)} 次）",
    )
    return False


__all__ = [
    "check_database_with_backoff",
    "send_ops_failure_alert",
    "send_ops_recovery_alert",
]
