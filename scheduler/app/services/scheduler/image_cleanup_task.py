"""
图片清理任务

功能：
1. 每20分钟执行一次
2. 清理各上传目录中「已删除业务对象」遗留的孤儿图片与超期文件：
   - 卡券图片目录 static/uploads/cards/    —— 卡券专属目录（仅卡券模块写入），卡券为硬删除
   - 素材库图片目录 static/uploads/products/ —— 与「单品发布上传」共用目录，素材为软删除(is_deleted)
   - 通用图片目录 static/uploads/images/    —— 多模块共用，孤儿扫描（引用清单覆盖关键词/确认收货/默认回复/反馈/收款码）
   - 图片关键词目录 static/uploads/keywords/ —— 关键词模块专属，孤儿扫描
   - 默认回复图片目录 default_reply/ 与 item_reply/ —— 默认回复模块专属，孤儿扫描
   - 人脸验证截图 uploads/face/ —— 按 mtime 超 7 天 TTL 删除（每次新登录自动替换旧截图，TTL 仅兜底）
   - 公开接口临时媒体 uploads/external_publish/ —— 按 mtime 超 30 天 TTL 删除（media_id 一次性使用）

安全设计（对应「千万不要删多了」的要求）：
- 专属目录（硬删除）：采用「孤儿扫描」，删除不被任何现存业务对象引用的文件。
  因该目录只有对应模块写入，孤儿即已删除对象遗留图片，安全。
- 共用目录（软删除）：绝不做「孤儿扫描」。products/ 目录还存放「单品发布」
  上传的图片/视频，这些文件不入素材表，若按孤儿扫描会被误删。故改为「精准引用删除」：
  仅删除「被已删除素材引用」且「未被任何未删除素材引用」的文件名，
  单品发布图片及其它非素材文件永不被触碰。
- images/ 共用目录引用清单不完整时整体跳过；保留期提高到 72 小时降低误删风险。
- 读取某类引用清单失败时，跳过该类清理（不执行任何删除），其它类正常进行。
- 保留期兜底：修改时间未超过保留期的文件（可能刚上传尚未保存）不会被删除。
- 仅删除图片扩展名文件，视频(.mp4等)与 .gitkeep 等标记文件一律跳过。
"""
import os
import json
import time
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.card import Card
from common.models.product_material import ProductMaterial
from common.models.user_setting import UserSetting

# 孤儿图片保留小时数：文件修改时间超过该时长且无引用才允许删除
# 目的：避免删除「刚上传落盘、但用户尚未保存到业务对象」的新图片
ORPHAN_RETENTION_HOURS = 24

# 允许清理的图片扩展名（小写，含点）；其它文件（视频、.gitkeep 等）一律跳过
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _basename_set_from(values) -> set[str]:
    """从一组图片路径/URL 中提取去目录化后的文件名集合"""
    names: set[str] = set()
    for value in values:
        if not value:
            continue
        name = os.path.basename(str(value).strip())
        if name:
            names.add(name)
    return names


class ImageCleanupTaskService:
    """图片清理任务服务（卡券 + 素材库）"""

    def __init__(self):
        self.task_name = "图片清理"

    def _resolve_static_root(self) -> Path:
        """
        解析静态文件根目录

        与项目其它服务保持一致：优先使用 STATIC_DIR 环境变量（Docker 共享卷），
        本地源码部署时回退到 backend-web/static。
        """
        static_env = os.environ.get("STATIC_DIR", "")
        if static_env:
            static_root = Path(static_env)
            if not static_root.is_absolute():
                static_root = Path.cwd() / static_root
            return static_root
        # 本地源码部署：scheduler/app/services/scheduler/ -> 项目根目录 -> backend-web/static
        project_root = Path(__file__).resolve().parents[4]
        return project_root / "backend-web" / "static"

    async def _card_referenced_filenames(self) -> set[str]:
        """收集所有卡券当前引用的图片文件名（image_url + image_urls）

        Raises:
            Exception: 数据库读取失败时向上抛出，由调用方跳过该目录清理
        """
        referenced: set[str] = set()
        async with async_session_maker() as session:
            stmt = select(Card.image_url, Card.image_urls)
            result = await session.execute(stmt)
            for image_url, image_urls in result.fetchall():
                referenced |= _basename_set_from([image_url])
                if image_urls:
                    # image_urls 为 JSON 数组字符串（Text 列）
                    try:
                        urls = json.loads(image_urls)
                    except (ValueError, TypeError):
                        urls = [image_urls]
                    if isinstance(urls, list):
                        referenced |= _basename_set_from(urls)
        return referenced

    async def _material_filenames_by_deleted(self, is_deleted: bool) -> set[str]:
        """收集指定删除状态的素材当前引用的图片文件名（images）

        Args:
            is_deleted: True 收集已删除素材的图片；False 收集未删除素材的图片

        Raises:
            Exception: 数据库读取失败时向上抛出，由调用方跳过素材清理
        """
        referenced: set[str] = set()
        async with async_session_maker() as session:
            stmt = select(ProductMaterial.images).where(
                ProductMaterial.is_deleted.is_(is_deleted)
            )
            result = await session.execute(stmt)
            for (images,) in result.fetchall():
                if not images:
                    continue
                # images 为 JSON 列，通常已是 list；兼容历史字符串存储
                if isinstance(images, str):
                    try:
                        images = json.loads(images)
                    except (ValueError, TypeError):
                        images = [images]
                if isinstance(images, list):
                    referenced |= _basename_set_from(images)
        return referenced

    async def _misc_referenced_filenames(self) -> set[str]:
        """收集 uploads/images/ 共用目录的全部已知引用文件名。

        覆盖所有已知会把本地上传图片 URL 存进数据库的字段：
        - xy_keyword_rules.image_url（图片关键词）
        - xy_confirm_receipt_messages.message_image（确认收货消息图片）
        - xy_default_replies.reply_image（账号/商品默认回复图片）
        - xy_feedbacks.images（意见反馈图片，JSON 数组）
        - xy_user_settings 中 key=payment_qrcode 的收款码图片

        Raises:
            Exception: 任一查询失败时向上抛出，由调用方跳过 images/ 目录清理
            （共用目录宁可少删，引用清单不完整时绝不删）
        """
        from common.models.confirm_receipt_message import ConfirmReceiptMessage
        from common.models.default_reply import DefaultReply
        from common.models.feedback import Feedback
        from common.models.xy_keyword_rule import XYKeywordRule

        referenced: set[str] = set()
        async with async_session_maker() as session:
            # 图片关键词
            result = await session.execute(select(XYKeywordRule.image_url))
            for (image_url,) in result.fetchall():
                referenced |= _basename_set_from([image_url])

            # 确认收货消息图片
            result = await session.execute(select(ConfirmReceiptMessage.message_image))
            for (message_image,) in result.fetchall():
                referenced |= _basename_set_from([message_image])

            # 默认回复图片（账号级与商品级共用字段）
            result = await session.execute(select(DefaultReply.reply_image))
            for (reply_image,) in result.fetchall():
                referenced |= _basename_set_from([reply_image])

            # 意见反馈图片（JSON 数组）
            result = await session.execute(select(Feedback.images))
            for (images,) in result.fetchall():
                if not images:
                    continue
                if isinstance(images, str):
                    try:
                        images = json.loads(images)
                    except (ValueError, TypeError):
                        images = [images]
                if isinstance(images, list):
                    referenced |= _basename_set_from(images)

            # 收款码图片（user_settings 的 payment_qrcode）
            result = await session.execute(
                select(UserSetting.value).where(UserSetting.key == "payment_qrcode")
            )
            for (value,) in result.fetchall():
                referenced |= _basename_set_from([value])
        return referenced

    async def _keyword_referenced_filenames(self) -> set[str]:
        """收集图片关键词引用的文件名（uploads/keywords/ 为关键词模块专属目录）。

        Raises:
            Exception: 查询失败时向上抛出，由调用方跳过该目录清理
        """
        from common.models.xy_keyword_rule import XYKeywordRule

        referenced: set[str] = set()
        async with async_session_maker() as session:
            result = await session.execute(select(XYKeywordRule.image_url))
            for (image_url,) in result.fetchall():
                referenced |= _basename_set_from([image_url])
        return referenced

    async def _reply_referenced_filenames(self) -> set[str]:
        """收集默认回复图片引用的文件名（default_reply/ 与 item_reply/ 为默认回复模块专属目录）。

        Raises:
            Exception: 查询失败时向上抛出，由调用方跳过该目录清理
        """
        from common.models.default_reply import DefaultReply

        referenced: set[str] = set()
        async with async_session_maker() as session:
            result = await session.execute(select(DefaultReply.reply_image))
            for (reply_image,) in result.fetchall():
                referenced |= _basename_set_from([reply_image])
        return referenced

    def _clean_dir_ttl(
        self,
        target_dir: Path,
        label: str,
        retention_hours: int,
        recursive: bool = False,
    ) -> dict:
        """
        按 TTL 清理目录（不区分引用关系，仅按文件 mtime 删除超期文件）。

        仅用于「无需引用判定的临时/诊断目录」：
        - uploads/face/：人脸验证截图（每次新登录自动替换旧截图，TTL 兜底）
        - uploads/external_publish/：公开接口临时媒体（按 media_id 使用，发布后即弃）

        Args:
            target_dir: 待清理目录
            label: 日志标识
            retention_hours: 保留期（小时）
            recursive: 是否递归清理子目录内文件（子目录本身保留）

        Returns:
            统计信息字典
        """
        stats = {"deleted": 0, "kept_ref": 0, "kept_recent": 0, "failed": 0, "freed": 0}

        if not target_dir.exists():
            logger.info(f"【{self.task_name}】{label}目录不存在，跳过: {target_dir}")
            return stats

        retention_seconds = retention_hours * 3600
        now = time.time()

        iterator = target_dir.rglob("*") if recursive else os.scandir(target_dir)
        entries = iterator if recursive else list(iterator)

        for entry in entries:
            try:
                path = Path(entry.path) if not isinstance(entry, Path) else entry
                if path.is_dir():
                    continue
                # 仅处理图片文件，跳过视频、.gitkeep 等
                if path.suffix.lower() not in IMAGE_EXTS:
                    continue
                stat = path.stat()
                if now - stat.st_mtime < retention_seconds:
                    stats["kept_recent"] += 1
                    continue
                file_size = stat.st_size
                os.remove(path)
                stats["deleted"] += 1
                stats["freed"] += file_size
                logger.info(f"【{self.task_name}】✓ 已删除{label}超期文件: {path.name}")
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"【{self.task_name}】✗ 处理文件失败: {entry}, 错误: {e}")

        return stats

    def _clean_dir(
        self,
        target_dir: Path,
        referenced: set[str],
        label: str,
        retention_hours: int = ORPHAN_RETENTION_HOURS,
    ) -> dict:
        """
        清理单个目录中的孤儿图片

        Args:
            target_dir: 待清理目录
            referenced: 需保护的文件名集合（被有效业务对象引用）
            label: 日志标识（如「卡券」「素材库」）
            retention_hours: 保留期（小时），文件 mtime 超过该时长且无引用才允许删除

        Returns:
            统计信息字典
        """
        stats = {"deleted": 0, "kept_ref": 0, "kept_recent": 0, "failed": 0, "freed": 0}

        if not target_dir.exists():
            logger.info(f"【{self.task_name}】{label}图片目录不存在，跳过: {target_dir}")
            return stats

        retention_seconds = retention_hours * 3600
        now = time.time()

        try:
            entries = list(os.scandir(target_dir))
        except Exception as e:
            logger.error(f"【{self.task_name}】{label}目录扫描失败: {e}")
            return stats

        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                filename = entry.name
                # 仅处理图片文件，跳过视频、.gitkeep 等
                if os.path.splitext(filename)[1].lower() not in IMAGE_EXTS:
                    continue
                # 被有效业务对象引用 -> 保护
                if filename in referenced:
                    stats["kept_ref"] += 1
                    continue
                stat = entry.stat()
                # 文件过新（可能刚上传尚未保存）-> 暂不删除
                if now - stat.st_mtime < retention_seconds:
                    stats["kept_recent"] += 1
                    continue
                file_size = stat.st_size
                os.remove(entry.path)
                stats["deleted"] += 1
                stats["freed"] += file_size
                logger.info(f"【{self.task_name}】✓ 已删除{label}孤儿图片: {filename}")
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"【{self.task_name}】✗ 处理文件失败: {entry.path}, 错误: {e}")

        return stats

    def _clean_targeted(
        self,
        target_dir: Path,
        deletable: set[str],
        label: str,
        retention_hours: int = ORPHAN_RETENTION_HOURS,
    ) -> dict:
        """精准删除：只删除文件名在 deletable 白名单内的文件（用于共用目录）

        与 _clean_dir（孤儿扫描）相反，本方法「默认保留」：只有明确列入 deletable
        的文件名才会被删除，目录中其它任何文件（如单品发布图片）一律不动。

        Args:
            target_dir: 待清理目录（与其它功能共用）
            deletable: 明确允许删除的文件名集合（= 已删除素材引用 - 未删除素材引用）
            label: 日志标识
            retention_hours: 保留期（小时），文件 mtime 超过该时长才允许删除

        Returns:
            统计信息字典
        """
        stats = {"deleted": 0, "kept_ref": 0, "kept_recent": 0, "failed": 0, "freed": 0}

        if not deletable:
            logger.info(f"【{self.task_name}】{label}无可删除的已删除对象图片，跳过")
            return stats

        if not target_dir.exists():
            logger.info(f"【{self.task_name}】{label}图片目录不存在，跳过: {target_dir}")
            return stats

        retention_seconds = retention_hours * 3600
        now = time.time()

        for filename in deletable:
            try:
                # 仅处理图片文件，跳过视频、.gitkeep 等
                if os.path.splitext(filename)[1].lower() not in IMAGE_EXTS:
                    continue
                file_path = target_dir / filename
                if not file_path.is_file():
                    continue
                stat = file_path.stat()
                # 文件过新（可能刚上传尚未保存）-> 暂不删除
                if now - stat.st_mtime < retention_seconds:
                    stats["kept_recent"] += 1
                    continue
                file_size = stat.st_size
                os.remove(file_path)
                stats["deleted"] += 1
                stats["freed"] += file_size
                logger.info(f"【{self.task_name}】✓ 已删除{label}已删除对象图片: {filename}")
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"【{self.task_name}】✗ 处理文件失败: {filename}, 错误: {e}")

        return stats

    async def execute(self):
        """执行图片清理任务"""
        logger.info(f"【{self.task_name}】开始执行")
        start_time = time.time()

        static_root = self._resolve_static_root()
        total = {"deleted": 0, "kept_ref": 0, "kept_recent": 0, "failed": 0, "freed": 0}

        # ===== 1. 清理卡券图片（卡券硬删除，引用清单=全部卡券）=====
        try:
            card_referenced = await self._card_referenced_filenames()
        except Exception as e:
            logger.error(f"【{self.task_name}】读取卡券图片引用失败，跳过卡券目录清理: {e}")
        else:
            logger.info(f"【{self.task_name}】卡券当前引用图片 {len(card_referenced)} 个")
            card_stats = self._clean_dir(static_root / "uploads" / "cards", card_referenced, "卡券")
            for k in total:
                total[k] += card_stats[k]

        # ===== 2. 清理素材库图片（素材软删除，products/ 与单品发布共用目录）=====
        # 精准删除：只删「被已删除素材引用」且「未被任何未删除素材引用」的文件，
        # 单品发布图片及其它非素材文件永不被触碰。
        try:
            live_referenced = await self._material_filenames_by_deleted(False)
            deleted_referenced = await self._material_filenames_by_deleted(True)
        except Exception as e:
            logger.error(f"【{self.task_name}】读取素材库图片引用失败，跳过素材库清理: {e}")
        else:
            # 被已删除素材引用、但仍被某个未删除素材引用的文件必须保留
            deletable = deleted_referenced - live_referenced
            logger.info(
                f"【{self.task_name}】未删除素材引用图片 {len(live_referenced)} 个，"
                f"已删除素材引用图片 {len(deleted_referenced)} 个，"
                f"可清理 {len(deletable)} 个"
            )
            material_stats = self._clean_targeted(
                static_root / "uploads" / "products", deletable, "素材库"
            )
            for k in total:
                total[k] += material_stats[k]

        # ===== 3. 清理通用图片目录 uploads/images/（多模块共用）=====
        # 孤儿扫描：引用清单覆盖全部已知本地图片引用字段（关键词/确认收货/默认回复/反馈/收款码）；
        # 任一引用收集失败 → 跳过本目录清理（共用目录宁可少删）；
        # 保留期提高到 72 小时，进一步降低误删刚上传文件的风险。
        try:
            misc_referenced = await self._misc_referenced_filenames()
        except Exception as e:
            logger.error(f"【{self.task_name}】读取通用图片引用失败，跳过 images/ 目录清理: {e}")
        else:
            logger.info(f"【{self.task_name}】通用图片当前引用 {len(misc_referenced)} 个")
            misc_stats = self._clean_dir(
                static_root / "uploads" / "images",
                misc_referenced,
                "通用图片",
                retention_hours=72,
            )
            for k in total:
                total[k] += misc_stats[k]

        # ===== 4. 清理图片关键词目录 uploads/keywords/（关键词模块专属，孤儿扫描）=====
        try:
            keyword_referenced = await self._keyword_referenced_filenames()
        except Exception as e:
            logger.error(f"【{self.task_name}】读取图片关键词引用失败，跳过 keywords/ 目录清理: {e}")
        else:
            logger.info(f"【{self.task_name}】图片关键词当前引用 {len(keyword_referenced)} 个")
            keyword_stats = self._clean_dir(
                static_root / "uploads" / "keywords", keyword_referenced, "图片关键词"
            )
            for k in total:
                total[k] += keyword_stats[k]

        # ===== 5. 清理默认回复图片目录 default_reply/ 与 item_reply/（默认回复模块专属）=====
        try:
            reply_referenced = await self._reply_referenced_filenames()
        except Exception as e:
            logger.error(f"【{self.task_name}】读取默认回复图片引用失败，跳过 default_reply/、item_reply/ 目录清理: {e}")
        else:
            logger.info(f"【{self.task_name}】默认回复图片当前引用 {len(reply_referenced)} 个")
            for sub_dir, label in (("default_reply", "默认回复"), ("item_reply", "商品回复")):
                reply_stats = self._clean_dir(
                    static_root / "uploads" / sub_dir, reply_referenced, label
                )
                for k in total:
                    total[k] += reply_stats[k]

        # ===== 6. 人脸验证截图 TTL 清理（7 天）=====
        # 截图每次新登录自动替换旧截图，TTL 仅兜底清理不再登录账号的历史截图
        face_stats = self._clean_dir_ttl(
            static_root / "uploads" / "face", "人脸验证截图", retention_hours=24 * 7
        )
        for k in total:
            total[k] += face_stats[k]

        # ===== 7. 公开接口临时媒体 TTL 清理（30 天，递归）=====
        # 公开接口上传的图片/规格图/视频为临时媒体（media_id 一次性使用），按 mtime 超期删除
        ext_stats = self._clean_dir_ttl(
            static_root / "uploads" / "external_publish",
            "公开接口媒体",
            retention_hours=24 * 30,
            recursive=True,
        )
        for k in total:
            total[k] += ext_stats[k]

        elapsed = time.time() - start_time
        freed_mb = total["freed"] / (1024 * 1024)
        logger.info(
            f"【{self.task_name}】清理完成，"
            f"已删除: {total['deleted']}, 仍被引用保留: {total['kept_ref']}, "
            f"未到保留期保留: {total['kept_recent']}, 失败: {total['failed']}, "
            f"释放空间: {freed_mb:.2f}MB, 耗时: {elapsed:.2f}秒"
        )


# 创建全局实例
image_cleanup_task_service = ImageCleanupTaskService()
