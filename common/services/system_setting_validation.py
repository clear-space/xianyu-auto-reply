"""
系统设置通用字段校验。

功能：
1. 校验日志保留天数
2. 校验金额、端口、枚举和邮箱类系统设置
3. 校验数据保留策略配置（保留天数/批大小/总开关）
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from common.utils.email_utils import normalize_email_address

# 数据保留策略配置键（统一数据保留引擎读取，见 common/services/data_retention_service.py）
DATA_RETENTION_PREFIX = "data_retention."
DATA_RETENTION_ENABLED_KEY = "data_retention.enabled"
DATA_RETENTION_BATCH_SIZE_KEY = "data_retention.cleanup_batch_size"
DATA_RETENTION_MAX_BATCHES_KEY = "data_retention.max_batches_per_table"
DATA_RETENTION_DAYS_KEYS = {
    "data_retention.token_renewal_log_days",
    "data_retention.cookies_refresh_log_days",
    "data_retention.auto_reply_message_log_days",
    "data_retention.default_reply_record_days",
    "data_retention.account_login_log_days",
    "data_retention.publish_log_days",
    "data_retention.risk_control_log_days",
    "data_retention.token_cache_soft_expired_days",
    "data_retention.ai_chat_message_days",
    "data_retention.goofish_crawl_item_days",
    "data_retention.scheduled_task_log_days",
    "data_retention.cleanup_log_days",
}


def validate_data_retention_setting(
    key: str,
    raw_value: str,
) -> tuple[str | None, str | None]:
    """校验数据保留策略配置值。

    返回 (规范化后的值, 错误消息)；错误消息为 None 表示校验通过。
    天数字段限 1~3650 天，批大小限 100~100000 行，批次数限 1~10000。
    """
    value = str(raw_value or "").strip()
    if key == DATA_RETENTION_ENABLED_KEY:
        if value.lower() not in {"true", "false"}:
            return None, "数据保留清理总开关必须是 true 或 false"
        return value.lower(), None
    if key == DATA_RETENTION_BATCH_SIZE_KEY:
        try:
            num = int(value)
            if 100 <= num <= 100000:
                return str(num), None
        except ValueError:
            pass
        return None, "单批删除行数必须在 100~100000 之间"
    if key == DATA_RETENTION_MAX_BATCHES_KEY:
        try:
            num = int(value)
            if 1 <= num <= 10000:
                return str(num), None
        except ValueError:
            pass
        return None, "单表单轮最大批次数必须在 1~10000 之间"
    if key in DATA_RETENTION_DAYS_KEYS:
        try:
            num = int(value)
            if 1 <= num <= 3650:
                return str(num), None
        except ValueError:
            pass
        return None, "保留天数必须在 1~3650 之间"
    return value, None


def parse_log_retention_days(raw_value: str) -> tuple[int | None, str | None]:
    """解析日志保留天数，范围为 1~365。"""
    value = str(raw_value or "").strip()
    if not value.isdigit():
        return None, "日志保留天数必须为1到365之间的整数"
    retention_days = int(value)
    if not 1 <= retention_days <= 365:
        return None, "日志保留天数必须为1到365之间的整数"
    return retention_days, None


def validate_decimal_setting(
    raw_value: str,
    *,
    field_name: str,
    allow_empty: bool = True,
    strictly_positive: bool = False,
) -> str | None:
    """校验金额或费率配置，最多保留两位小数。"""
    value = str(raw_value or "").strip()
    if not value:
        return None if allow_empty else f"{field_name}不能为空"
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return f"{field_name}必须为有效数字，且最多保留两位小数"
    if not number.is_finite() or number.as_tuple().exponent < -2:
        return f"{field_name}必须为有效数字，且最多保留两位小数"
    if strictly_positive and number <= 0:
        return f"{field_name}必须大于0，且最多保留两位小数"
    if not strictly_positive and number < 0:
        return f"{field_name}不能小于0，且最多保留两位小数"
    return None


def validate_business_setting(key: str, raw_value: str) -> str | None:
    """校验通用接口可修改的数值、枚举与邮箱设置。"""
    value = str(raw_value or "").strip()
    if key == "smtp_port" and (not value.isdigit() or not 1 <= int(value) <= 65535):
        return "SMTP端口必须为1到65535之间的整数"
    if key == "smtp_user" and value:
        _, error_message = normalize_email_address(value)
        return "发件邮箱格式不正确" if error_message else None
    if key == "withdraw.notify_email" and value:
        _, error_message = normalize_email_address(value)
        return "提现通知邮箱格式不正确" if error_message else None
    if key == "withdraw.min_amount":
        return validate_decimal_setting(value, field_name="最低提现金额")
    if key == "distribution.fee_type" and value not in {"fixed", "percent"}:
        return "手续费类型无效，请选择固定金额或按订单金额百分比"
    if key == "distribution.fee_rate":
        return validate_decimal_setting(value, field_name="手续费")
    if key == "user.renew_month_price":
        return validate_decimal_setting(value, field_name="续期单价", strictly_positive=True)
    if key == "user.register_default_days" and value and (not value.isdigit() or int(value) <= 0):
        return "注册默认天数必须为正整数，留空表示不设置到期日"
    return None
