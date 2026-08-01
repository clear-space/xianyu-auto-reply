"""
数据库恢复路由

功能：
1. 上传 .sql.gz 备份文件并解析
2. 选择已有备份文件解析
3. 列出备份目录下的文件
4. 按分类执行数据库恢复

所有端点仅管理员可访问。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api import deps
from app.services.restore_service import RESTORE_CATEGORY_MAP, RestoreService
from common.models.user import User
from common.utils.backup_paths import get_backup_root

router = APIRouter(prefix="/restore", tags=["数据库恢复"])


class ParseExistingRequest(BaseModel):
    file_name: str = Field(..., description="备份文件名")


class ExecuteRequest(BaseModel):
    reference_id: str = Field(..., description="解析阶段返回的 reference_id")
    categories: list[str] = Field(..., min_length=1, description="要恢复的分类 key 列表")


class PreviewRequest(BaseModel):
    file_name: str = Field(..., description="备份文件名或 reference_id")


# ==================== 上传并解析 ====================


@router.post("/upload-parse")
async def upload_and_parse(
    file: UploadFile = File(...),
    _: User = Depends(deps.get_current_admin_user),
    service: RestoreService = Depends(deps.get_restore_service),
) -> dict:
    """上传 .sql.gz 备份文件并解析其内容。

    返回文件中的表列表和按分类组织的信息。
    上传的文件暂存到 backups/_uploads/ 目录。
    """
    # 验证文件扩展名
    filename = file.filename or "upload.gz"
    if not filename.endswith(".gz") and not filename.endswith(".sql.gz"):
        return {"success": False, "message": "仅支持 .sql.gz 格式的备份文件", "data": None}

    try:
        data = await file.read()
        if not data:
            return {"success": False, "message": "上传的文件为空", "data": None}

        # 检查是否为文本 gzip（不是二进制压缩的误判）
        file_path = RestoreService.save_uploaded_file(data, filename)
    except ValueError as exc:
        return {"success": False, "message": str(exc), "data": None}
    except Exception as exc:
        return {"success": False, "message": f"保存文件失败: {exc}", "data": None}

    try:
        result = RestoreService.parse_backup_file(file_path)
        result["reference_id"] = file_path.name
        return {"success": True, "data": result}
    except ValueError as exc:
        # 解析失败时清理文件
        RestoreService.cleanup_uploaded_file(file_path.name)
        return {"success": False, "message": str(exc), "data": None}
    except FileNotFoundError as exc:
        return {"success": False, "message": str(exc), "data": None}


# ==================== 解析已有备份 ====================


@router.post("/parse-existing")
async def parse_existing(
    body: ParseExistingRequest,
    _: User = Depends(deps.get_current_admin_user),
    service: RestoreService = Depends(deps.get_restore_service),
) -> dict:
    """解析磁盘上已有的备份文件。

    文件必须位于备份目录中。
    """
    try:
        file_path = RestoreService.resolve_file_path(body.file_name)
    except (ValueError, FileNotFoundError) as exc:
        return {"success": False, "message": str(exc), "data": None}

    try:
        result = RestoreService.parse_backup_file(file_path)
        result["reference_id"] = body.file_name
        return {"success": True, "data": result}
    except ValueError as exc:
        return {"success": False, "message": str(exc), "data": None}
    except FileNotFoundError as exc:
        return {"success": False, "message": str(exc), "data": None}


# ==================== 预览备份统计 ====================


@router.post("/preview")
async def preview_backup(
    body: PreviewRequest,
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """预览备份文件统计信息（每张表的数据行数，按分类汇总）。

    与 parse-existing 不同，本接口会完整扫描备份文件以统计行数，
    适合在恢复前了解备份内容概况。
    """
    try:
        file_path = RestoreService.resolve_file_path(body.file_name)
    except (ValueError, FileNotFoundError) as exc:
        return {"success": False, "message": str(exc), "data": None}

    try:
        result = RestoreService.preview_backup_file(file_path)
        return {"success": True, "data": result}
    except ValueError as exc:
        return {"success": False, "message": str(exc), "data": None}
    except FileNotFoundError as exc:
        return {"success": False, "message": str(exc), "data": None}


# ==================== 列出备份文件 ====================


@router.get("/backup-files")
async def list_backup_files(
    _: User = Depends(deps.get_current_admin_user),
) -> dict:
    """列出备份目录中所有可用的 .sql.gz 文件。"""
    try:
        files = RestoreService.list_backup_files()
        return {"success": True, "data": files}
    except Exception as exc:
        return {"success": False, "message": f"列出备份文件失败: {exc}", "data": []}


# ==================== 下载备份文件 ====================


@router.get("/backup-files/{file_name}/download")
async def download_backup_file(
    file_name: str,
    _: User = Depends(deps.get_current_admin_user),
) -> StreamingResponse:
    """下载备份目录中的 .sql.gz 文件（仅管理员）。

    文件不存在时返回统一的错误结构（HTTP 200 + success=False）。
    """
    # 安全检查：拒绝路径穿越
    if "/" in file_name or "\\" in file_name or ".." in file_name:
        return {"success": False, "message": "无效的文件名", "data": None}

    root = get_backup_root()
    file_path = (root / file_name).resolve()
    try:
        file_path.relative_to(root.resolve())
    except ValueError:
        return {"success": False, "message": "无效的文件路径", "data": None}

    if not file_path.is_file():
        return {"success": False, "message": "备份文件不存在或已被删除", "data": None}

    def iter_file():
        with file_path.open("rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    disposition = f"attachment; filename*=UTF-8''{quote(file_name)}"
    return StreamingResponse(
        iter_file(),
        media_type="application/gzip",
        headers={"Content-Disposition": disposition},
    )


# ==================== 执行恢复 ====================


@router.post("/execute")
async def execute_restore(
    body: ExecuteRequest,
    _: User = Depends(deps.get_current_admin_user),
    service: RestoreService = Depends(deps.get_restore_service),
) -> dict:
    """按选定的分类执行数据库恢复。

    恢复将覆盖当前数据库中的对应表数据。
    单表失败不影响其他表，失败详情在 failed_tables 中返回。
    """
    # 解析文件路径
    try:
        file_path = RestoreService.resolve_file_path(body.reference_id)
    except (ValueError, FileNotFoundError) as exc:
        return {"success": False, "message": str(exc), "data": None}

    # 验证分类
    valid_categories = set(RESTORE_CATEGORY_MAP.keys())
    invalid = [c for c in body.categories if c not in valid_categories]
    if invalid:
        return {
            "success": False,
            "message": f"无效的恢复类别: {', '.join(invalid)}",
            "data": None,
        }

    try:
        result = await service.execute_restore(file_path, body.categories)
        result["categories_restored"] = body.categories

        # 如果是上传的暂存文件，恢复后清理
        if body.reference_id.startswith("upload_"):
            RestoreService.cleanup_uploaded_file(body.reference_id)

        return {"success": True, "data": result}
    except ValueError as exc:
        return {"success": False, "message": str(exc), "data": None}
    except FileNotFoundError as exc:
        return {"success": False, "message": str(exc), "data": None}
    except Exception as exc:
        return {"success": False, "message": f"恢复执行失败: {exc}", "data": None}


