"""
文本处理通用工具

功能:
1. XSS 转义 - 对外部输入文本进行 HTML 实体转义，防止跨站脚本注入

注意: 此模块只放与文本处理相关的纯函数，与密码哈希/JWT 无关。
密码哈希、JWT 令牌等安全功能请放在 ``common/utils/security.py``。
"""
from __future__ import annotations

import html
import re

# 商品标题/卡券名称的前缀编号格式：一个字母 + 三位数字（如 A014）
# (?!\d) 保证数字恰好三位：A0145 这类四位数字整体视为格式不符
_PREFIX_NUMBER_RE = re.compile(r"^([A-Za-z])(\d{3})(?!\d)")


def extract_prefix_number(text: str | None) -> tuple[str, int] | None:
    """提取文本开头的前缀编号（字母+三位数字，如 "A014 手机壳" → ("A", 14)）。

    供商品标题与卡券名称的编号匹配、定时发布去重共用同一份解析规则。
    格式不符（无编号/不足三位数字等）返回 None。
    """
    if not text:
        return None
    match = _PREFIX_NUMBER_RE.match(str(text).strip())
    if not match:
        return None
    return match.group(1).upper(), int(match.group(2))


def escape_xss(text: str | None) -> str | None:
    """对文本进行 HTML 实体转义，防止 XSS 注入。

    Args:
        text: 待转义的原始文本，允许为 ``None`` 或空字符串。

    Returns:
        转义后的安全文本；当 ``text`` 为 ``None`` 或空时原样返回，便于上层链式处理。
    """
    if not text:
        return text
    return html.escape(text)


def safe_str(obj: object) -> str:
    """安全地将任意对象（通常是异常）转换为字符串。

    某些异常/对象的 ``__str__`` 自身可能抛错（例如携带异常的 repr），
    这里依次尝试 ``str()`` → ``repr()``，全部失败时返回兜底文案，
    保证日志记录等场景永不因转换字符串而二次抛错。

    Args:
        obj: 待转换的对象，通常是捕获到的异常实例。

    Returns:
        对象的字符串表示；无法转换时返回 ``"未知错误"``。
    """
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return "未知错误"


__all__ = ["escape_xss", "safe_str"]
