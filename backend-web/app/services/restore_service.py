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
from common.utils.security import get_password_hash

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

    async def execute_restore(self, file_path: Path, category_keys: list[str]) -> dict:
        """按分类执行数据库恢复。

        Args:
            file_path: 备份文件路径
            category_keys: 要恢复的分类 key 列表，["all"] 表示全部

        Returns:
            {restored_tables, skipped_tables, failed_tables, total_rows_inserted, total_duration_ms}

        设计要点：
        - 使用 text() 执行所有 SQL，转义 \\:word 防止被误解析为 bind parameter
        - 原子表替换：先恢复到 {table}_restore，全部成功后再 RENAME 原子替换
        - 恢复完成后显式 rollback + commit，重置 session 的事务状态
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"备份文件不存在: {file_path}")

        # 解析选中的表
        if "all" in category_keys:
            selected_tables: set[str] | None = None  # None = 全部
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

        try:
            await self.session.execute(text("SET FOREIGN_KEY_CHECKS=0;"))

            current_table: str | None = None
            in_current_table = False
            current_sql_parts: list[str] = []

            async def _flush_current_table():
                """原子性恢复单张表。"""
                nonlocal in_current_table, total_rows

                if not current_table or not in_current_table:
                    return

                match = selected_tables is None or current_table in selected_tables
                if not match:
                    skipped_tables.append(current_table)
                    in_current_table = False
                    current_sql_parts.clear()
                    return

                restore_table = f"{current_table}_restore"
                try:
                    # 1. 清理可能残留的临时表
                    await _exec_sql(f"DROP TABLE IF EXISTS `{restore_table}`")

                    # 2. 将原表名替换为临时表名，执行全部 DDL + DML
                    combined = "\n".join(current_sql_parts)
                    combined_restore = combined.replace(
                        f"`{current_table}`", f"`{restore_table}`"
                    )
                    statements = combined_restore.split(";\n")
                    for stmt in statements:
                        stmt = stmt.strip()
                        if not stmt:
                            continue
                        if stmt.startswith("--"):
                            continue
                        await _exec_sql(stmt)
                        if stmt.upper().startswith("INSERT"):
                            total_rows += 1

                    # 3. 原子性替换
                    result = await self.session.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl"
                        ),
                        {"tbl": current_table},
                    )
                    original_exists = result.scalar() > 0

                    if original_exists:
                        await _exec_sql(
                            f"RENAME TABLE `{current_table}` TO `{current_table}_old`, "
                            f"`{restore_table}` TO `{current_table}`"
                        )
                        await _exec_sql(f"DROP TABLE IF EXISTS `{current_table}_old`")
                    else:
                        await _exec_sql(
                            f"RENAME TABLE `{restore_table}` TO `{current_table}`"
                        )

                    restored_tables.append(current_table)
                    logger.info(f"恢复表 {current_table} 完成（原子模式）")
                except Exception as exc:
                    error_msg = str(exc)[:500]
                    logger.error(f"恢复表 {current_table} 失败: {error_msg}")
                    failed_tables.append({"table": current_table, "error": error_msg})
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
                finally:
                    in_current_table = False
                    current_sql_parts.clear()

            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    m = _DROP_TABLE_RE.search(line)
                    if m:
                        await _flush_current_table()
                        current_table = m.group(1)
                        in_current_table = True
                        current_sql_parts = [line.rstrip("\n")]
                        continue

                    if in_current_table:
                        if current_sql_parts:
                            current_sql_parts.append(line.rstrip("\n"))

                await _flush_current_table()

            await self.session.execute(text("SET FOREIGN_KEY_CHECKS=1;"))

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

        # 如果选中了表但全失败了，列出被跳过的表以方便排查
        if selected_tables is not None and not restored_tables and not failed_tables:
            skipped_tables = list(selected_tables)

        return {
            "restored_tables": restored_tables,
            "skipped_tables": skipped_tables,
            "failed_tables": failed_tables,
            "total_rows_inserted": total_rows,
            "total_duration_ms": duration_ms,
        }

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
