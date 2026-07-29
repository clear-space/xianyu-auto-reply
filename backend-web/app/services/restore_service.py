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

    # ==================== 执行恢复 ====================

    async def execute_restore(self, file_path: Path, category_keys: list[str]) -> dict:
        """按分类执行数据库恢复。

        Args:
            file_path: 备份文件路径
            category_keys: 要恢复的分类 key 列表，["all"] 表示全部

        Returns:
            {restored_tables, skipped_tables, failed_tables, total_rows_inserted, total_duration_ms}
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

            # "other" 动态计算：备份中不在任何已知分类中的表
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

        try:
            # 开始恢复：禁用外键检查
            await self.session.execute(text("SET FOREIGN_KEY_CHECKS=0;"))

            current_table: str | None = None
            in_current_table = False
            current_sql_parts: list[str] = []

            async def _flush_current_table():
                """将当前表的累积 SQL 执行到数据库"""
                nonlocal in_current_table, total_rows

                if not current_table or not in_current_table:
                    return

                match = selected_tables is None or current_table in selected_tables
                if not match:
                    skipped_tables.append(current_table)
                    in_current_table = False
                    current_sql_parts.clear()
                    return

                is_log = _is_log_table(current_table)
                try:
                    combined = "\n".join(current_sql_parts)
                    # DDL (DROP + CREATE) 作为整体执行
                    # INSERT 逐条执行避免大事务
                    statements = combined.split(";\n")
                    for stmt in statements:
                        stmt = stmt.strip()
                        if not stmt:
                            continue
                        # 跳过注释行
                        if stmt.startswith("--"):
                            continue
                        await self.session.execute(text(stmt))
                        if stmt.upper().startswith("INSERT"):
                            total_rows += 1  # 每条 INSERT 是一批数据

                    restored_tables.append(current_table)
                    logger.info(f"恢复表 {current_table} 完成")
                except Exception as exc:
                    error_msg = str(exc)[:500]
                    logger.error(f"恢复表 {current_table} 失败: {error_msg}")
                    failed_tables.append({"table": current_table, "error": error_msg})
                    # 回滚当前表的部分执行
                    try:
                        await self.session.rollback()
                    except Exception:
                        pass
                finally:
                    in_current_table = False
                    current_sql_parts.clear()

            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    # 检测新表开始
                    m = _DROP_TABLE_RE.search(line)
                    if m:
                        # 刷新上一个表
                        await _flush_current_table()
                        current_table = m.group(1)
                        in_current_table = True
                        current_sql_parts = [line.rstrip("\n")]
                        continue

                    if in_current_table:
                        # 跳过备份文件头部的 SET / 注释
                        if current_sql_parts:
                            current_sql_parts.append(line.rstrip("\n"))

                # 刷新最后一个表
                await _flush_current_table()

            # 恢复外键检查
            await self.session.execute(text("SET FOREIGN_KEY_CHECKS=1;"))

        except Exception as exc:
            logger.error(f"恢复执行异常: {exc}")
            try:
                await self.session.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
            except Exception:
                pass
            raise
        finally:
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
