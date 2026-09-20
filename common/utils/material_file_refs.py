"""
素材库文件引用提取工具

素材图片/视频与单品发布上传共用 static/uploads/products/ 目录。删除素材时
需要精确判断「本地文件是否还被其它有效素材引用」才能安全级联删除文件，
本模块统一从素材各字段（images/videos/specifications/versions）提取
引用的本地文件名，供 backend-web 删除级联清理与 scheduler 图片清理任务
共用，避免两处解析逻辑漂移导致漏删或误删。

安全约定：仅统计 static/uploads/products/ 目录内的本地上传文件，
CDN/外链 URL 一律忽略（其 basename 可能与本地文件重名，纳入反而危险）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable, Set

# 素材图片/视频落盘目录在 URL 中的特征片段
# （同时覆盖 /static/uploads/products/ 与绝对路径 .../static/uploads/products/，
#   Windows 反斜杠在匹配前先归一化）
_LOCAL_PRODUCTS_MARKER = "uploads/products/"


def _as_json(value: Any) -> Any:
    """JSON 列兼容：历史数据可能存字符串，尝试反序列化；已是 list/dict 直接返回。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not (text.startswith("[") or text.startswith("{")):
        return value
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return value


def _iter_items(container: Any) -> Iterable[Any]:
    """把 JSON 列值（list/dict/字符串）归一化为可迭代的顶层元素。"""
    value = _as_json(container)
    if isinstance(value, (list, tuple)):
        yield from value
    elif value:
        yield value


def _iter_urls(images: Any, videos: Any, specifications: Any, versions: Any) -> Iterable[Any]:
    """遍历素材全部字段中可能引用本地文件的 URL 值。"""
    # 主行 images（始终镜像默认版本）
    yield from _iter_items(images)
    # 多版本 versions[].images（非默认版本图片只存在这里）
    for version_item in _iter_items(versions):
        if isinstance(version_item, dict):
            yield from _iter_items(version_item.get("images"))
    # 视频 videos[].{url,path}
    for video_item in _iter_items(videos):
        if isinstance(video_item, dict):
            for key in ("url", "path"):
                if video_item.get(key):
                    yield video_item[key]
    # 规格图 specifications[].values[].image
    for spec_item in _iter_items(specifications):
        if not isinstance(spec_item, dict):
            continue
        for value_item in _iter_items(spec_item.get("values")):
            if isinstance(value_item, dict) and value_item.get("image"):
                yield value_item["image"]


def extract_file_basenames(
    images: Any = None,
    videos: Any = None,
    specifications: Any = None,
    versions: Any = None,
) -> Set[str]:
    """提取素材引用的本地商品文件名集合（纯文件名，去目录/去查询串）。

    仅统计本地上传目录（static/uploads/products/）内的文件；外链/其它目录
    一律忽略。供删除级联清理（backend-web）与孤儿清理任务（scheduler）共用。
    """
    names: Set[str] = set()
    for url_value in _iter_urls(images, videos, specifications, versions):
        if not url_value:
            continue
        text = str(url_value).strip().replace("\\", "/")
        if _LOCAL_PRODUCTS_MARKER not in text.lower():
            continue
        name = os.path.basename(text.split("?")[0])
        if name:
            names.add(name)
    return names
