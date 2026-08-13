"""
数据库恢复服务

功能：
1. 解析 .sql.gz 备份文件，识别其中的表并按分类组织
2. 按用户选择的分类执行数据恢复
3. 列出备份目录下已有的备份文件

设计要点：
- 流式解析：逐行扫描 gzip 文件，不整段加载到内存
- 执行隔离：单表失败不影响其他表，记录到 failed_tables
- 安全：FOREIGN_KEY_CHECKS 控制，解析阶段校验文件有效性
"""
from __future__ import annotations

import gzip
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.utils.backup_paths import get_backup_root, ensure_backup_root

# 匹配 :word 模式（SQLAlchemy text() 的 bind parameter 语法）
# 备份 SQL 中的 JSON 值（如 "category_id": 636）会导致 text() 报错
# "A value is required for bind parameter '636'"
_BIND_PARAM_RE = re.compile(r":(\w+)")

# DROP TABLE IF EXISTS `tablename` 正则
_DROP_TABLE_RE = re.compile(r"DROP\s+TABLE\s+IF\s+EXISTS\s+`(\w+)`", re.IGNORECASE)

# INSERT INTO `tablename` 正则
_INSERT_RE = re.compile(r"INSERT\s+INTO\s+`(\w+)`", re.IGNORECASE)

# 上传文件暂存子目录（前缀 _ 避免与 scheduler 备份文件混淆）
_UPLOAD_SUBDIR = "_uploads"

# 上传文件最大大小 200MB
_MAX_UPLOAD_SIZE = 200 * 1024 * 1024

# ==================== 数据分类映射 ====================

RESTORE_CATEGORY_MAP: dict[str, dict] = {
    "all": {
        "label": "全部数据",
        "tables": None,  # None = 备份中所有表
    },
    "system_config": {
        "label": "系统配置",
        "tables": [
            "xy_system_settings", "xy_scheduled_tasks",
            "xy_cookie_refresh_schedules", "xy_auto_rate_configs",
            "xy_notification_channels", "xy_confirm_receipt_messages",
        ],
    },
    "users_accounts": {
        "label": "用户与账号",
        "tables": [
            "xy_users", "xy_user_settings", "xy_accounts", "xy_token_cache",
            "xy_shared_scan_sessions", "xy_shared_scan_workers",
            "xy_risk_control_logs", "xy_account_login_logs",
            "xy_delivery_block_rules", "xy_personal_blacklist", "xy_platform_blacklist",
        ],
    },
    "core_business": {
        "label": "业务核心",
        "tables": [
            "xy_catalog_items", "xy_orders", "xy_cards", "xy_card_item_relations",
            "xy_keyword_rules", "xy_default_replies", "xy_default_reply_records",
            "xy_ai_chat_messages", "xy_chat_quick_phrases",
            "xy_message_filters", "xy_message_notifications",
        ],
    },
    "ads_feedback": {
        "label": "广告与反馈",
        "tables": [
            "xy_advertisements", "xy_announcements", "xy_popup_announcements",
            "xy_feedbacks", "xy_feedback_messages",
        ],
    },
    "distribution_finance": {
        "label": "分销与财务",
        "tables": [
            "xy_dock_records", "xy_dock_code_bindings", "xy_agent_orders",
            "xy_fund_flows", "xy_recharge_orders", "xy_settlement_records",
            "xy_activation_logs",
        ],
    },
    "publish_crawl": {
        "label": "商品发布与采集",
        "tables": [
            "xy_product_materials", "xy_publish_addresses", "xy_user_publish_addresses",
            "xy_goofish_crawl_jobs", "xy_goofish_crawl_items",
            "xy_listing_monitor_categories", "xy_listing_monitor_tasks",
            "xy_listing_monitor_items", "xy_listing_monitor_logs",
            "xy_order_fallback_accounts", "xy_collect_fallback_accounts",
        ],
    },
    "commission_system": {
        "label": "返佣系统",
        "tables": [
            "fy_accounts", "fy_delete_rules", "fy_materials",
            "fy_product_rules", "fy_publish_rules",
        ],
    },
    "other": {
        "label": "其他（未归类的表）",
        "tables": None,  # 动态计算：备份中不在任何已知分类中的表
    },
}

# 所有已命名分类中的表名集合（不含 "all" 和 "other"）
_ALL_KNOWN_TABLES: set[str] = set()
for _cat_key, _cat_info in RESTORE_CATEGORY_MAP.items():
    if _cat_key in ("all", "other"):
        continue
    _tbls = _cat_info.get("tables")
    if _tbls:
        _ALL_KNOWN_TABLES.update(_tbls)

# ==================== 账号数据分类（多模式恢复） ====================
#
# 恢复模式说明：
# - all（全部恢复）: 恢复备份中所有表（系统账号表除外，见 _NEVER_RESTORE_TABLES）
# - shared（恢复公用数据）: 只恢复公用表；账号专属表跳过；混合表仅恢复全局行
# - selected_accounts（按账号恢复）: 公用表整表恢复 + 账号专属/混合表按所选账号行级恢复
#
# 账号专属表：数据行完全绑定某个闲鱼账号，筛选列值非空。
# 关联关系（已核实）：
#   xy_accounts.account_id (String) = WebSocket cookie_id = 账号唯一标识
#   xy_accounts.id (BigInteger)     = 其他表的 account_pk 外键
#   xy_accounts.unb                 = myid = xy_token_cache.user_id
ACCOUNT_FILTER_COLUMNS: dict[str, str] = {
    "xy_accounts": "account_id",
    "xy_token_cache": "user_id",  # unb（myid）
    "xy_keyword_rules": "account_pk",
    "xy_catalog_items": "account_pk",
    "xy_message_notifications": "account_pk",
    "xy_default_replies": "account_id",
    "xy_default_reply_records": "account_id",
    "xy_orders": "account_id",
    "xy_confirm_receipt_messages": "account_id",
    "xy_cookie_refresh_schedules": "account_id",
    "xy_auto_rate_configs": "account_id",
    "xy_message_filters": "account_id",
    "xy_ai_chat_messages": "cookie_id",
    "xy_delivery_block_rules": "account_id",
    "fy_delete_rules": "account_id",
    "fy_publish_rules": "account_id",
}

# 混合表：同时包含"全局行"（筛选列为 NULL）与"账号行"（筛选列为具体账号）
MIXED_FILTER_COLUMNS: dict[str, str] = {
    "xy_publish_addresses": "account_id",  # account_id 空表示全局通用地址
    "fy_materials": "account_id",          # account_id 空表示全局素材
    "fy_product_rules": "account_id",      # account_id 空表示全局商品规则
    "xy_personal_blacklist": "account_id", # account_id 空表示全局黑名单
}

# 所有账号关联表（专属 + 混合）
_ACCOUNT_RELATED_TABLES: frozenset[str] = frozenset(
    set(ACCOUNT_FILTER_COLUMNS) | set(MIXED_FILTER_COLUMNS)
)

# 任何模式下都绝不恢复的表（系统账号密码永不随备份迁移）
_NEVER_RESTORE_TABLES: frozenset[str] = frozenset({"xy_users"})

# 日志类表（仅结构无数据），按备份文件生成逻辑 _is_log_table 保持一致
_LOG_TABLE_SUFFIXES = ("_log", "_logs")

# 表名 → 中文标签映射（预览功能用），仅列出用户关心的核心表
PREVIEW_TABLE_LABELS: dict[str, str] = {
    # 用户与账号
    "xy_users": "用户", "xy_accounts": "账号", "xy_token_cache": "Token 缓存",
    "xy_user_settings": "用户设置",
    # 业务核心
    "xy_catalog_items": "商品目录", "xy_orders": "订单", "xy_cards": "卡卷",
    "xy_card_item_relations": "卡卷关联", "xy_keyword_rules": "关键词规则",
    "xy_default_replies": "默认回复", "xy_default_reply_records": "回复记录",
    "xy_ai_chat_messages": "AI 聊天消息", "xy_chat_quick_phrases": "快捷短语",
    # 广告与反馈
    "xy_advertisements": "广告", "xy_announcements": "公告",
    "xy_popup_announcements": "弹窗公告", "xy_feedbacks": "反馈",
    "xy_feedback_messages": "反馈消息",
    # 分销与财务
    "xy_agent_orders": "代理订单", "xy_fund_flows": "资金流水",
    "xy_recharge_orders": "充值订单", "xy_settlement_records": "结算记录",
    "xy_dock_records": "对接记录", "xy_dock_code_bindings": "邀请码绑定",
    "xy_activation_logs": "激活日志",
    # 商品发布与采集
    "xy_product_materials": "商品素材", "xy_publish_addresses": "发布地址",
    "xy_user_publish_addresses": "用户发布地址",
    "xy_goofish_crawl_jobs": "采集任务", "xy_goofish_crawl_items": "采集商品",
    "xy_listing_monitor_tasks": "监控任务", "xy_listing_monitor_items": "监控商品",
    # 返佣
    "fy_accounts": "返佣账号", "fy_materials": "返佣素材",
    "fy_product_rules": "返佣商品规则", "fy_publish_rules": "返佣发布规则",
    # 系统配置
    "xy_system_settings": "系统设置", "xy_scheduled_tasks": "定时任务",
    "xy_cookie_refresh_schedules": "Cookie 刷新计划",
    # 风控/黑名单
    "xy_risk_control_logs": "风控日志", "xy_personal_blacklist": "个人黑名单",
    "xy_platform_blacklist": "平台黑名单", "xy_account_login_logs": "登录日志",
    "xy_delivery_block_rules": "发货拦截规则",
    # 消息
    "xy_message_filters": "消息过滤", "xy_message_notifications": "消息通知",
    "xy_notification_channels": "通知渠道", "xy_confirm_receipt_messages": "确认收货消息",
    "xy_auto_rate_configs": "自动评价配置",
    # 其他
    "xy_shared_scan_sessions": "共享扫码会话", "xy_shared_scan_workers": "共享扫码工作器",
    "xy_order_fallback_accounts": "订单回落账号", "xy_collect_fallback_accounts": "采集回落账号",
    "xy_listing_monitor_categories": "监控分类", "xy_listing_monitor_logs": "监控日志",
    "fy_delete_rules": "返佣删除规则",
}


def _is_log_table(table_name: str) -> bool:
    """判断是否为日志类表"""
    name = table_name.lower()
    return name.endswith(_LOG_TABLE_SUFFIXES)


# ==================== SQL 行解析工具（行级恢复用） ====================

def _split_sql_value_list(values_str: str) -> list[str]:
    """按逗号分割 SQL VALUES 列表，正确处理字符串内的逗号与转义。

    备份文件由 _format_value 生成，字符串统一使用单引号包裹并转义
    （\\\\、\\'、\\n、\\r），因此可按引号状态做可靠分词。

    Args:
        values_str: VALUES 关键字后括号内的原始文本（不含外层括号）

    Returns:
        值 token 列表（保留原始 SQL 字面量形式，如 'abc'、123、NULL）
    """
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    escape = False
    for ch in values_str:
        if escape:
            current.append(ch)
            escape = False
        elif ch == "\\" and in_string:
            current.append(ch)
            escape = True
        elif ch == "'":
            in_string = not in_string
            current.append(ch)
        elif ch == "," and not in_string:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _parse_insert_line(line: str) -> tuple[list[str], list[str]] | None:
    """解析备份文件中的一行 INSERT 语句。

    Args:
        line: 形如 "INSERT INTO `t` (`c1`, `c2`) VALUES (v1, v2);" 的完整行

    Returns:
        (列名列表, 值 token 列表)；无法解析时返回 None
    """
    m = _INSERT_RE.search(line)
    if not m:
        return None
    open_idx = line.find("(", m.end())
    close_idx = line.find(")", open_idx)
    if open_idx == -1 or close_idx == -1:
        return None
    cols = [c.strip().strip("`") for c in line[open_idx + 1:close_idx].split(",")]

    vm = re.search(r"\)\s*VALUES\s*\(", line, re.IGNORECASE)
    if not vm:
        return None
    vstart = vm.end()
    vend = line.rfind(")")  # 行尾最后一个 ) 即值列表闭合括号
    if vend <= vstart:
        return None
    values = _split_sql_value_list(line[vstart:vend])
    return cols, values


def _normalize_sql_value(token: str) -> str | int | None:
    """将 SQL 值 token 还原为 Python 值。

    - NULL → None
    - '字符串' → 去除引号并反转义
    - 纯数字 → int
    - 其余（0x.. 等）→ 原样字符串
    """
    token = token.strip()
    if token.upper() == "NULL":
        return None
    if token.startswith("'"):
        inner = token[1:-1]
        inner = (
            inner.replace("\\'", "'")
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
        )
        return inner
    try:
        return int(token)
    except ValueError:
        return token


def _sql_string_literal(value: str) -> str:
    """将 Python 字符串转为 SQL 单引号字面量（与备份格式一致的转义）。"""
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f"'{escaped}'"


class RestoreService:
    """数据库恢复服务"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ==================== 解析备份文件 ====================

    @staticmethod
    def parse_backup_file(file_path: Path) -> dict:
        """解析 .sql.gz 备份文件，返回表列表和分类信息。

        Args:
            file_path: .sql.gz 文件的绝对路径

        Returns:
            {
                "source_file": str,
                "total_tables": int,
                "tables": [{"name": str, "has_data": bool}],
                "categories": [{key, label, table_count, tables}],
            }
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"备份文件不存在: {file_path}")

        table_names: list[str] = []
        tables_with_data: set[str] = set()

        try:
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    m = _DROP_TABLE_RE.search(line)
                    if m:
                        table_names.append(m.group(1))
                        continue
                    m = _INSERT_RE.search(line)
                    if m:
                        tables_with_data.add(m.group(1))
        except gzip.BadGzipFile:
            raise ValueError("文件不是有效的 gzip 格式")

        if not table_names:
            raise ValueError("未在备份文件中找到任何数据表")

        # 去重（保留首次出现顺序）
        seen: set[str] = set()
        unique_tables: list[str] = []
        for t in table_names:
            if t not in seen:
                seen.add(t)
                unique_tables.append(t)

        # 构建表详情
        table_details = [
            {
                "name": t,
                "has_data": t in tables_with_data,
                "has_structure": True,  # 所有表都有结构
            }
            for t in unique_tables
        ]

        # 构建反向索引：表名 -> 分类
        table_to_category: dict[str, str] = {}
        for cat_key, cat_info in RESTORE_CATEGORY_MAP.items():
            if cat_key == "all":
                continue
            for tbl in (cat_info.get("tables") or []):
                if tbl not in table_to_category:
                    table_to_category[tbl] = cat_key

        # 按分类组织
        categories: dict[str, dict] = {}
        unclassified: list[dict] = []

        for td in table_details:
            cat_key = table_to_category.get(td["name"])
            if cat_key is None:
                unclassified.append(td)
                continue
            if cat_key not in categories:
                categories[cat_key] = {
                    "key": cat_key,
                    "label": RESTORE_CATEGORY_MAP[cat_key]["label"],
                    "table_count": 0,
                    "tables": [],
                }
            categories[cat_key]["tables"].append(td)
            categories[cat_key]["table_count"] += 1

        # 未分类表归入 "其他"
        if unclassified:
            categories["other"] = {
                "key": "other",
                "label": "其他",
                "table_count": len(unclassified),
                "tables": unclassified,
            }

        # 按定义顺序返回分类
        ordered_categories = []
        for cat_key in RESTORE_CATEGORY_MAP:
            if cat_key == "all":
                continue
            if cat_key in categories:
                ordered_categories.append(categories[cat_key])
        if "other" in categories:
            ordered_categories.append(categories["other"])

        return {
            "source_file": file_path.name,
            "total_tables": len(unique_tables),
            "tables": table_details,
            "categories": ordered_categories,
        }

    @staticmethod
    def parse_accounts(file_path: Path) -> list[dict]:
        """解析备份文件中 xy_accounts 表的数据，返回账号列表。

        用于「按账号恢复」模式的账号多选界面。

        Args:
            file_path: .sql.gz 文件的绝对路径

        Returns:
            [{account_id, display_name, unb, id}]
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"备份文件不存在: {file_path}")

        accounts: list[dict] = []
        in_accounts_section = False

        try:
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    m = _DROP_TABLE_RE.search(line)
                    if m:
                        in_accounts_section = m.group(1) == "xy_accounts"
                        continue
                    if not in_accounts_section:
                        continue

                    parsed = _parse_insert_line(line)
                    if not parsed:
                        continue
                    cols, values = parsed

                    def _get(col: str):
                        if col not in cols:
                            return None
                        idx = cols.index(col)
                        if idx >= len(values):
                            return None
                        return _normalize_sql_value(values[idx])

                    account_id = _get("account_id")
                    if account_id is None:
                        continue
                    accounts.append({
                        "account_id": str(account_id),
                        "display_name": str(_get("display_name") or ""),
                        "unb": str(_get("unb") or ""),
                        "id": _get("id"),
                    })
        except gzip.BadGzipFile:
            raise ValueError("文件不是有效的 gzip 格式")

        return accounts

    # ==================== 预览备份统计 ====================

    @staticmethod
    def preview_backup_file(file_path: Path) -> dict:
        """流式扫描 .sql.gz 备份文件，统计每张表的数据行数。

        与 parse_backup_file() 不同，本方法额外统计每张表的 INSERT 行数
        （即数据行数），并按分类汇总。适用于在恢复前预览备份内容。

        Args:
            file_path: .sql.gz 文件的绝对路径

        Returns:
            {
                "source_file": str,
                "file_size": int,
                "file_size_formatted": str,
                "total_rows": int,
                "categories": [{key, label, table_count, total_rows, tables: [{name, label, rows}]}],
            }
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"备份文件不存在: {file_path}")

        file_size = file_path.stat().st_size

        # 每张表的行数统计
        table_rows: dict[str, int] = {}  # table_name → row count
        current_table: str | None = None

        try:
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    # 遇到新的 DROP TABLE → 切换当前表
                    m = _DROP_TABLE_RE.search(line)
                    if m:
                        current_table = m.group(1)
                        if current_table not in table_rows:
                            table_rows[current_table] = 0
                        continue

                    # 累计 INSERT 行数
                    if current_table:
                        m = _INSERT_RE.search(line)
                        if m:
                            table_rows[current_table] += 1
        except gzip.BadGzipFile:
            raise ValueError("文件不是有效的 gzip 格式")

        if not table_rows:
            raise ValueError("未在备份文件中找到任何数据表")

        # 构建表详情列表
        all_table_details: list[dict] = []
        for tbl_name, rows in table_rows.items():
            all_table_details.append({
                "name": tbl_name,
                "label": PREVIEW_TABLE_LABELS.get(tbl_name, ""),
                "rows": rows,
            })

        # 按分类组织
        table_to_category: dict[str, str] = {}
        for cat_key, cat_info in RESTORE_CATEGORY_MAP.items():
            if cat_key == "all":
                continue
            for tbl in (cat_info.get("tables") or []):
                if tbl not in table_to_category:
                    table_to_category[tbl] = cat_key

        categories: dict[str, dict] = {}
        unclassified: list[dict] = []
        total_rows = 0

        for td in all_table_details:
            total_rows += td["rows"]
            cat_key = table_to_category.get(td["name"])
            if cat_key is None:
                unclassified.append(td)
                continue
            if cat_key not in categories:
                categories[cat_key] = {
                    "key": cat_key,
                    "label": RESTORE_CATEGORY_MAP[cat_key]["label"],
                    "table_count": 0,
                    "total_rows": 0,
                    "tables": [],
                }
            categories[cat_key]["tables"].append(td)
            categories[cat_key]["table_count"] += 1
            categories[cat_key]["total_rows"] += td["rows"]

        # 未归类的归入 "other"
        if unclassified:
            other_rows = sum(t["rows"] for t in unclassified)
            categories["other"] = {
                "key": "other",
                "label": "其他（未归类的表）",
                "table_count": len(unclassified),
                "total_rows": other_rows,
                "tables": unclassified,
            }

        # 按 RESTORE_CATEGORY_MAP 定义顺序返回
        ordered_categories = []
        for cat_key in RESTORE_CATEGORY_MAP:
            if cat_key == "all":
                continue
            if cat_key in categories:
                ordered_categories.append(categories[cat_key])
        if "other" in categories:
            ordered_categories.append(categories["other"])

        return {
            "source_file": file_path.name,
            "file_size": file_size,
            "file_size_formatted": RestoreService._format_size(file_size),
            "total_rows": total_rows,
            "categories": ordered_categories,
        }

    # ==================== 执行恢复 ====================

    async def execute_restore(
        self,
        file_path: Path,
        category_keys: list[str] | None = None,
        *,
        mode: str = "all",
        account_ids: list[str] | None = None,
    ) -> dict:
        """执行数据库恢复。

        支持两种调用方式：
        1. 旧版分类恢复：category_keys 非空时按分类选择表
        2. 新版模式恢复：mode 指定
           - all（全部恢复）: 恢复备份中所有表（系统账号表 _NEVER_RESTORE_TABLES 除外）
           - shared（恢复公用数据）: 公用表整表恢复；账号专属表跳过；
             混合表仅恢复全局行（筛选列为 NULL）；不触碰目标环境日志表
           - selected_accounts（按账号恢复）: 公用表整表恢复；账号专属/混合表
             按所选账号做行级合并（DELETE 目标旧行 + INSERT 备份行），
             不影响目标环境其他账号的数据

        Args:
            file_path: 备份文件路径
            category_keys: 旧版按分类恢复的 key 列表（["all"] 表示全部）
            mode: 恢复模式（category_keys 为空时生效）
            account_ids: mode=selected_accounts 时选择的闲鱼账号 account_id 列表

        Returns:
            {restored_tables, skipped_tables, failed_tables, total_rows_inserted,
             total_duration_ms, account_results}

        设计要点：
        - 使用 text() 执行所有 SQL，转义 \\:word 防止被误解析为 bind parameter
        - 公用表：原子表替换（先恢复到 {table}_restore，成功后再 RENAME 替换）
        - 账号表：行级合并（DELETE + INSERT，事务包裹，保留目标库其他账号数据）
        - 恢复完成后显式 rollback + commit，重置 session 的事务状态
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"备份文件不存在: {file_path}")

        if mode not in ("all", "shared", "selected_accounts"):
            raise ValueError(f"无效的恢复模式: {mode}")

        legacy = bool(category_keys)

        # ---------- 旧版分类：解析选中的表 ----------
        selected_tables: set[str] | None = None
        if legacy:
            if "all" in category_keys:
                selected_tables = None  # None = 全部
            else:
                selected_tables = set()
                for cat_key in category_keys:
                    cat_info = RESTORE_CATEGORY_MAP.get(cat_key)
                    if cat_info and cat_info.get("tables"):
                        selected_tables.update(cat_info["tables"])

                # "other" 动态计算
                if "other" in category_keys:
                    backup_table_names = set()
                    with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                        for line in fp:
                            m = _DROP_TABLE_RE.search(line)
                            if m:
                                backup_table_names.add(m.group(1))
                    other_tables = backup_table_names - _ALL_KNOWN_TABLES
                    selected_tables.update(other_tables)
                    logger.info(f"动态解析 '其他' 分类: {len(other_tables)} 张表 -> {sorted(other_tables)}")

            if selected_tables is not None and not selected_tables:
                raise ValueError("未选择任何有效的恢复类别")

        # ---------- 按账号模式：解析所选账号 ----------
        sel_account_ids: set[str] = set()
        sel_account_pks: set[int] = set()
        sel_unbs: set[str] = set()
        account_results: dict = {"restored_accounts": [], "missing_accounts": []}

        if mode == "selected_accounts":
            if not account_ids:
                raise ValueError("按账号恢复模式需要至少选择一个账号")
            backup_accounts = self.parse_accounts(file_path)
            backup_map = {a["account_id"]: a for a in backup_accounts}
            selected_accounts = []
            for aid in account_ids:
                if aid in backup_map:
                    selected_accounts.append(backup_map[aid])
                else:
                    account_results["missing_accounts"].append(aid)
            if not selected_accounts:
                raise ValueError("所选账号均不存在于备份文件中")
            sel_account_ids = {a["account_id"] for a in selected_accounts}
            sel_account_pks = {
                int(a["id"]) for a in selected_accounts if isinstance(a.get("id"), int)
            }
            sel_unbs = {a["unb"] for a in selected_accounts if a.get("unb")}
            account_results["restored_accounts"] = sorted(sel_account_ids)
            logger.info(
                f"按账号恢复: 选择 {len(sel_account_ids)} 个账号, "
                f"pk={sorted(sel_account_pks)}, unb={sorted(sel_unbs)}"
            )

        # ---------- 每张表的处理动作 ----------
        def _table_action(table: str) -> tuple[str, str | None]:
            """返回 (action, filter_column)。

            action:
              atomic         整表原子替换（公用表）
              rows_account   行级合并（账号专属表，按所选账号筛选）
              rows_mixed     行级合并（混合表，全局行 + 所选账号行）
              rows_global    行级合并（混合表，仅全局行）
              skip / skip_never / skip_account / skip_log  跳过
            """
            if table in _NEVER_RESTORE_TABLES:
                return "skip_never", None
            if legacy:
                match = selected_tables is None or table in selected_tables
                return ("atomic", None) if match else ("skip", None)
            if mode == "all":
                return "atomic", None
            if table in ACCOUNT_FILTER_COLUMNS:
                if mode == "shared":
                    return "skip_account", None
                return "rows_account", ACCOUNT_FILTER_COLUMNS[table]
            if table in MIXED_FILTER_COLUMNS:
                if mode == "shared":
                    return "rows_global", MIXED_FILTER_COLUMNS[table]
                return "rows_mixed", MIXED_FILTER_COLUMNS[table]
            if _is_log_table(table):
                # shared/selected 模式不触碰目标环境的日志表
                return "skip_log", None
            return "atomic", None

        # ---------- JWT 密钥保护 ----------
        # 系统设置表将被恢复时，先保存目标环境当前的 JWT 密钥，恢复完成后回写。
        # 否则后端重启后会从库中读到备份来源的密钥，全员登录态失效。
        if legacy:
            will_restore_system_settings = (
                selected_tables is None or "xy_system_settings" in selected_tables
            )
        else:
            will_restore_system_settings = True  # 模式路径下 xy_system_settings 为公用表

        preserved_jwt_secret: str | None = None
        if will_restore_system_settings:
            try:
                result = await self.session.execute(
                    text(
                        "SELECT `value` FROM xy_system_settings "
                        "WHERE `key` = 'security.jwt_secret_key' LIMIT 1"
                    )
                )
                preserved_jwt_secret = result.scalar_one_or_none()
            except Exception as exc:
                logger.warning(f"读取当前 JWT 密钥失败（跳过保护）: {exc}")

        start_time = time.monotonic()

        restored_tables: list[str] = []
        skipped_tables: list[str] = []
        failed_tables: list[dict] = []
        total_rows = 0

        # Helper: 执行一条备份 SQL
        # 使用 text() 但先转义 :word 防止被当成 bind parameter
        # （备份数据中的 JSON 如 "category_id": 636 会被 text() 解析为 :636）
        async def _exec_sql(sql: str) -> None:
            escaped = _BIND_PARAM_RE.sub(r"\\:\1", sql)
            await self.session.execute(text(escaped))

        def _build_row_matcher(
            filter_column: str, *, include_global: bool, value_set: set, as_int: bool
        ):
            """构建行筛选函数：返回 (cols, values) -> bool"""
            def matcher(cols: list[str], values: list[str]) -> bool:
                if filter_column not in cols:
                    return False
                idx = cols.index(filter_column)
                if idx >= len(values):
                    return False
                norm = _normalize_sql_value(values[idx])
                if norm is None:
                    return include_global
                if as_int:
                    return norm in value_set
                return str(norm) in value_set

            return matcher

        def _build_delete_sql(
            table: str,
            filter_column: str,
            *,
            include_global: bool,
            value_set: set,
            as_int: bool,
        ) -> str | None:
            """构建目标表旧行删除 SQL（无删除条件时返回 None）。"""
            conds: list[str] = []
            if include_global:
                conds.append(f"`{filter_column}` IS NULL")
            if value_set:
                if as_int:
                    conds.append(
                        f"`{filter_column}` IN ({','.join(str(v) for v in sorted(value_set))})"
                    )
                else:
                    conds.append(
                        f"`{filter_column}` IN ("
                        + ",".join(_sql_string_literal(v) for v in sorted(value_set))
                        + ")"
                    )
            if not conds:
                return None
            return f"DELETE FROM `{table}` WHERE {' OR '.join(conds)}"

        def _rebuild_insert(
            table: str, cols: list[str], values: list[str], keep_id: bool
        ) -> str:
            """重建 INSERT 语句；除 xy_accounts 外去掉 id 列，让目标库自增主键，
            避免备份来源的自增 id 与目标库其他账号数据冲突。"""
            new_cols, new_values = cols, values
            if not keep_id and "id" in new_cols:
                idx = new_cols.index("id")
                new_cols = new_cols[:idx] + new_cols[idx + 1:]
                new_values = new_values[:idx] + new_values[idx + 1:]
            col_clause = ", ".join(f"`{c}`" for c in new_cols)
            val_clause = ", ".join(new_values)
            return f"INSERT INTO `{table}` ({col_clause}) VALUES ({val_clause});"

        def _row_value_set(filter_column: str | None) -> tuple[set, bool]:
            """根据筛选列名返回匹配值集合与是否为整型。

            - account_pk → 所选账号的主键 id 集合（整型比较）
            - user_id（token 缓存） → 所选账号的 unb 集合（字符串比较）
            - 其余（account_id / cookie_id） → 所选账号的 account_id 集合
            """
            if filter_column == "account_pk":
                return sel_account_pks, True
            if filter_column == "user_id":
                return sel_unbs, False
            return sel_account_ids, False

        try:
            await self.session.execute(text("SET FOREIGN_KEY_CHECKS=0;"))

            current_table: str | None = None
            in_current_table = False
            current_action: str | None = None
            current_filter_col: str | None = None
            # 原子模式缓冲
            current_sql_parts: list[str] = []
            # 行级模式缓冲：DDL 行（CREATE 语句，目标表缺失时建表用）+ 解析后的 INSERT 行
            current_ddl_lines: list[str] = []
            current_row_lines: list[tuple[list[str], list[str]]] = []

            async def _flush_atomic(table: str):
                """原子性恢复单张表（整表替换）。"""
                nonlocal total_rows

                restore_table = f"{table}_restore"
                try:
                    # 1. 清理可能残留的临时表
                    await _exec_sql(f"DROP TABLE IF EXISTS `{restore_table}`")

                    # 2. 将原表名替换为临时表名，执行全部 DDL + DML
                    combined = "\n".join(current_sql_parts)
                    combined_restore = combined.replace(
                        f"`{table}`", f"`{restore_table}`"
                    )
                    # 按 ";\n" 切分语句后，逐块剔除注释行再执行。
                    # 注意：不能按「块首是否以 -- 开头」整块跳过——备份中每张表的
                    # 第一条 INSERT 前都紧跟 "-- 表数据" 注释行，整块跳过会丢失
                    # 每张表的第一行数据（历史 bug）。
                    statements = combined_restore.split(";\n")
                    for stmt in statements:
                        stmt_lines = [
                            l for l in stmt.split("\n")
                            if l.strip() and not l.strip().startswith("--")
                        ]
                        cleaned = "\n".join(stmt_lines).strip()
                        if not cleaned:
                            continue
                        await _exec_sql(cleaned)
                        if cleaned.upper().startswith("INSERT"):
                            total_rows += 1

                    # 3. 原子性替换
                    result = await self.session.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl"
                        ),
                        {"tbl": table},
                    )
                    original_exists = result.scalar() > 0

                    if original_exists:
                        await _exec_sql(
                            f"RENAME TABLE `{table}` TO `{table}_old`, "
                            f"`{restore_table}` TO `{table}`"
                        )
                        await _exec_sql(f"DROP TABLE IF EXISTS `{table}_old`")
                    else:
                        await _exec_sql(
                            f"RENAME TABLE `{restore_table}` TO `{table}`"
                        )

                    restored_tables.append(table)
                    logger.info(f"恢复表 {table} 完成（原子模式）")
                except Exception as exc:
                    error_msg = str(exc)[:500]
                    logger.error(f"恢复表 {table} 失败: {error_msg}")
                    failed_tables.append({"table": table, "error": error_msg})
                    # 清理临时表
                    try:
                        await _exec_sql(f"DROP TABLE IF EXISTS `{restore_table}`")
                    except Exception:
                        pass
                    # 回滚 DML（临时表上的 INSERT 等）
                    try:
                        await self.session.rollback()
                    except Exception:
                        pass

            async def _flush_row_table(
                table: str,
                filter_column: str,
                *,
                include_global: bool,
                value_set: set,
                as_int: bool,
                keep_id: bool,
            ):
                """行级合并恢复单张表：DELETE 目标旧行 + INSERT 备份匹配行。"""
                nonlocal total_rows

                if not current_row_lines:
                    skipped_tables.append(table)
                    return

                matcher = _build_row_matcher(
                    filter_column,
                    include_global=include_global,
                    value_set=value_set,
                    as_int=as_int,
                )
                matched = [
                    (c, v) for c, v in current_row_lines if matcher(c, v)
                ]
                if not matched:
                    skipped_tables.append(table)
                    return

                delete_sql = _build_delete_sql(
                    table,
                    filter_column,
                    include_global=include_global,
                    value_set=value_set,
                    as_int=as_int,
                )

                try:
                    # 清理既有事务状态，开启显式事务
                    await self.session.rollback()

                    # 目标表存在性检查
                    result = await self.session.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl"
                        ),
                        {"tbl": table},
                    )
                    table_exists = result.scalar() > 0

                    if not table_exists:
                        # 目标库缺表：用备份中的 CREATE 语句直接建表。
                        # SHOW CREATE TABLE 输出的 DDL 是多行的，必须整段拼接执行，
                        # 不能只取以 CREATE 开头的第一行。
                        create_sql = "\n".join(current_ddl_lines).strip()
                        if not create_sql:
                            raise RuntimeError("备份中缺少 CREATE TABLE 语句，无法在目标库建表")
                        await _exec_sql(create_sql)

                    # xy_accounts 保留 id（其他表经 account_pk 引用），
                    # 插入前检查备份行 id 是否被目标库其他账号占用
                    if keep_id and table_exists:
                        ids: list[int] = []
                        for c, v in matched:
                            if "id" in c:
                                idx = c.index("id")
                                if idx < len(v):
                                    norm = _normalize_sql_value(v[idx])
                                    if isinstance(norm, int):
                                        ids.append(norm)
                        if ids:
                            result = await self.session.execute(
                                text(
                                    f"SELECT id, account_id FROM `{table}` "
                                    f"WHERE id IN ({','.join(str(i) for i in ids)})"
                                )
                            )
                            for row in result.all():
                                if str(row.account_id) not in sel_account_ids:
                                    raise RuntimeError(
                                        f"主键冲突: id={row.id} 已被账号 {row.account_id} 占用，"
                                        f"无法迁移所选账号（建议迁移到全新环境）"
                                    )

                    if delete_sql:
                        await _exec_sql(delete_sql)

                    inserted = 0
                    for c, v in matched:
                        await _exec_sql(_rebuild_insert(table, c, v, keep_id))
                        inserted += 1

                    await self.session.commit()
                    restored_tables.append(table)
                    total_rows += inserted
                    logger.info(f"恢复表 {table} 完成（行级合并模式，{inserted} 行）")
                except Exception as exc:
                    error_msg = str(exc)[:500]
                    logger.error(f"恢复表 {table} 失败: {error_msg}")
                    failed_tables.append({"table": table, "error": error_msg})
                    try:
                        await self.session.rollback()
                    except Exception:
                        pass

            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    m = _DROP_TABLE_RE.search(line)
                    if m:
                        # 处理上一个表
                        if in_current_table and current_table:
                            if current_action == "atomic":
                                await _flush_atomic(current_table)
                            elif current_action in (
                                "rows_account", "rows_mixed", "rows_global",
                            ):
                                value_set, as_int = _row_value_set(current_filter_col)
                                await _flush_row_table(
                                    current_table,
                                    current_filter_col,
                                    include_global=current_action != "rows_account",
                                    value_set=value_set,
                                    as_int=as_int,
                                    keep_id=(current_table == "xy_accounts"),
                                )
                        # 开启新表
                        current_table = m.group(1)
                        current_action, current_filter_col = _table_action(current_table)
                        current_sql_parts.clear()
                        current_ddl_lines.clear()
                        current_row_lines.clear()
                        in_current_table = current_action in (
                            "atomic", "rows_account", "rows_mixed", "rows_global",
                        )
                        if current_action in (
                            "skip", "skip_never", "skip_account", "skip_log",
                        ):
                            skipped_tables.append(current_table)
                        continue

                    if not in_current_table:
                        continue

                    stripped = line.rstrip("\n")
                    if current_action == "atomic":
                        current_sql_parts.append(stripped)
                    else:
                        parsed = _parse_insert_line(stripped)
                        if parsed:
                            current_row_lines.append(parsed)
                        elif stripped and not stripped.startswith("--"):
                            current_ddl_lines.append(stripped)

                # 处理最后一个表
                if in_current_table and current_table:
                    if current_action == "atomic":
                        await _flush_atomic(current_table)
                    elif current_action in (
                        "rows_account", "rows_mixed", "rows_global",
                    ):
                        value_set, as_int = _row_value_set(current_filter_col)
                        await _flush_row_table(
                            current_table,
                            current_filter_col,
                            include_global=current_action != "rows_account",
                            value_set=value_set,
                            as_int=as_int,
                            keep_id=(current_table == "xy_accounts"),
                        )

            await self.session.execute(text("SET FOREIGN_KEY_CHECKS=1;"))

            # ---------- JWT 密钥保护回写 ----------
            if preserved_jwt_secret and "xy_system_settings" in restored_tables:
                try:
                    await self.session.rollback()
                    update_sql = (
                        "UPDATE xy_system_settings SET `value` = "
                        + _sql_string_literal(preserved_jwt_secret)
                        + " WHERE `key` = 'security.jwt_secret_key'"
                    )
                    await _exec_sql(update_sql)
                    await self.session.commit()
                    logger.info("JWT 密钥保护: 已回写为目标环境的密钥，登录态不受备份迁移影响")
                except Exception as exc:
                    logger.warning(f"JWT 密钥保护回写失败: {exc}")

        except Exception as exc:
            logger.error(f"恢复执行异常: {exc}")
            try:
                await self.session.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
            except Exception:
                pass
            raise
        finally:
            # 关键：显式回滚再提交，清除因 DDL 隐式提交导致的
            # SQLAlchemy 事务状态不一致问题
            # 这样 session 归还连接池时连接是干净的
            try:
                await self.session.rollback()
            except Exception:
                pass
            try:
                await self.session.commit()
            except Exception:
                pass

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # 旧版分类：如果选中了表但全失败了，列出被选中的表以方便排查
        if legacy and selected_tables is not None and not restored_tables and not failed_tables:
            skipped_tables = list(selected_tables)

        result = {
            "restored_tables": restored_tables,
            "skipped_tables": skipped_tables,
            "failed_tables": failed_tables,
            "total_rows_inserted": total_rows,
            "total_duration_ms": duration_ms,
        }
        if not legacy:
            result["account_results"] = account_results
        return result

    # ==================== 备份文件列表 ====================

    @staticmethod
    def list_backup_files() -> list[dict]:
        """列出备份目录下所有 .sql.gz 文件，按修改时间降序。

        Returns:
            [{name, size, size_formatted, modified_at}]
        """
        root = get_backup_root()
        files: list[dict] = []
        if not root.is_dir():
            return files

        for f in sorted(root.glob("backup_*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                st = f.stat()
                files.append({
                    "name": f.name,
                    "size": st.st_size,
                    "size_formatted": RestoreService._format_size(st.st_size),
                    "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                })
            except OSError:
                continue
        return files

    @staticmethod
    def _format_size(size: int) -> str:
        """字节数 -> 易读文本"""
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.2f} MB"
        return f"{size / 1024 / 1024 / 1024:.2f} GB"

    # ==================== 上传文件处理 ====================

    @staticmethod
    def save_uploaded_file(file_data: bytes, original_filename: str) -> Path:
        """保存上传的备份文件到暂存目录。

        Args:
            file_data: 文件二进制内容
            original_filename: 原始文件名

        Returns:
            暂存文件的绝对路径
        """
        if len(file_data) > _MAX_UPLOAD_SIZE:
            raise ValueError(f"文件大小超过限制（最大 {_MAX_UPLOAD_SIZE // 1024 // 1024} MB）")

        # 安全文件名
        safe_name = original_filename or "upload.sql.gz"
        if not safe_name.endswith(".sql.gz"):
            safe_name = safe_name.rsplit(".", 1)[0] if "." in safe_name else safe_name
            safe_name += ".sql.gz"

        upload_dir = get_backup_root() / _UPLOAD_SUBDIR
        upload_dir.mkdir(parents=True, exist_ok=True)

        # 加时间戳避免覆盖
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_name = f"upload_{timestamp}_{safe_name}"
        dest_path = upload_dir / dest_name

        with open(dest_path, "wb") as f:
            f.write(file_data)

        # 验证 gzip 有效性
        try:
            with gzip.open(dest_path, "rb") as test_fp:
                test_fp.read(1024)  # 只读开头验证
        except gzip.BadGzipFile:
            dest_path.unlink(missing_ok=True)
            raise ValueError("文件不是有效的 gzip 格式")

        return dest_path

    @staticmethod
    def resolve_file_path(reference_id: str) -> Path:
        """根据 reference_id 解析文件路径（支持上传文件和已有备份文件）。

        Args:
            reference_id: 文件名（如 "upload_20260728_143022_backup.sql.gz"）

        Returns:
            文件绝对路径
        """
        # 安全检查：拒绝路径穿越
        if "/" in reference_id or "\\" in reference_id or ".." in reference_id:
            raise ValueError("无效的文件引用")

        # 先查上传目录
        upload_path = get_backup_root() / _UPLOAD_SUBDIR / reference_id
        if upload_path.is_file():
            return upload_path

        # 再查备份目录
        backup_path = get_backup_root() / reference_id
        if backup_path.is_file():
            return backup_path

        raise FileNotFoundError(f"参考文件不存在或已过期: {reference_id}")

    @staticmethod
    def cleanup_uploaded_file(reference_id: str) -> None:
        """清理上传的暂存文件"""
        upload_path = get_backup_root() / _UPLOAD_SUBDIR / reference_id
        try:
            if upload_path.is_file():
                upload_path.unlink()
        except OSError:
            pass
