"""
敏感字段加密模块（connection.dat 的密码字段）

功能：
1. Windows：使用 DPAPI（CryptProtectData/CryptUnprotectData）用户级加密，
   密文仅当前 Windows 用户可解，免密钥管理
2. 其它平台：回退 AES-256-GCM（pycryptodome），密钥由机器码经 PBKDF2 派生，
   随机 12 字节 nonce + 16 字节认证标签，防篡改
3. 加密结果带前缀标记（dpapi:/enc1:），无前缀视为旧版 Base64 数据，
   由 config_store 在加载时自动迁移

设计要点：
- 机器绑定：非 Windows 平台密钥依赖机器码，拷贝 connection.dat 到其它机器无法解密
- 兼容旧数据：旧版 Base64 编码的密码仍可读取，首次加载成功后自动升级为密文
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import sys

_PREFIX_DPAPI = "dpapi:"
_PREFIX_AES = "enc1:"

# AES-GCM（非 Windows 平台回退）参数
_PBKDF2_ITERATIONS = 200_000
_SALT = b"xianyu-auto-reply/connection.dat/v1"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _machine_key() -> bytes:
    """由机器码派生的 32 字节密钥种子（机器绑定）。"""
    from launcher.hardware_id import generate_machine_id

    return hashlib.sha256(generate_machine_id().encode("utf-8")).digest()


def _aes_key() -> bytes:
    """PBKDF2 派生 AES-256 密钥。"""
    from Crypto.Hash import SHA256
    from Crypto.Protocol.KDF import PBKDF2

    return PBKDF2(
        _machine_key(), _SALT, dkLen=32,
        count=_PBKDF2_ITERATIONS, hmac_hash_module=SHA256,
    )


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_encrypt(plaintext: str) -> str:
    """DPAPI 加密（仅 Windows 调用）。"""
    data_in = plaintext.encode("utf-8")
    buf_in = ctypes.create_string_buffer(data_in)
    blob_in = _DATA_BLOB(len(data_in), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "xianyu-auto-reply connection config",
        None, None, None, 0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("CryptProtectData 调用失败")
    try:
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return _PREFIX_DPAPI + base64.b64encode(encrypted).decode("ascii")


def _dpapi_decrypt(encoded: str) -> str:
    """DPAPI 解密（仅 Windows 调用）。"""
    raw = base64.b64decode(encoded)
    buf_in = ctypes.create_string_buffer(raw)
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None, None, None, None, 0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("CryptUnprotectData 调用失败")
    try:
        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return decrypted.decode("utf-8")


def _aes_encrypt(plaintext: str) -> str:
    """AES-256-GCM 加密（非 Windows 平台回退）。"""
    from Crypto.Cipher import AES

    nonce = os.urandom(12)
    cipher = AES.new(_aes_key(), AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    payload = nonce + tag + ciphertext
    return _PREFIX_AES + base64.b64encode(payload).decode("ascii")


def _aes_decrypt(encoded: str) -> str:
    """AES-256-GCM 解密（非 Windows 平台回退）。"""
    from Crypto.Cipher import AES

    payload = base64.b64decode(encoded)
    nonce, tag, ciphertext = payload[:12], payload[12:28], payload[28:]
    cipher = AES.new(_aes_key(), AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")


def encrypt_secret(plaintext: str) -> str:
    """加密敏感字段；空值原样返回。"""
    if not plaintext:
        return ""
    if _is_windows():
        return _dpapi_encrypt(plaintext)
    return _aes_encrypt(plaintext)


def decrypt_secret(encoded: str) -> str:
    """解密敏感字段。

    支持三种格式：
    - dpapi: 前缀 → DPAPI 密文
    - enc1:  前缀 → AES-256-GCM 密文
    - 无前缀 → 旧版 Base64 数据（兼容历史配置，返回解码后的明文）
    空值原样返回。
    """
    if not encoded:
        return ""
    text = str(encoded)
    if text.startswith(_PREFIX_DPAPI):
        return _dpapi_decrypt(text[len(_PREFIX_DPAPI):])
    if text.startswith(_PREFIX_AES):
        return _aes_decrypt(text[len(_PREFIX_AES):])
    # 旧版 Base64 数据（无前缀），按兼容方式解码；解码失败按原样返回
    try:
        return base64.b64decode(text.encode("utf-8")).decode("utf-8")
    except Exception:
        return text


def is_encrypted_secret(encoded: str) -> bool:
    """判断字段是否为新版密文（用于 config_store 判断是否需要迁移）。"""
    if not encoded:
        return True
    text = str(encoded)
    return text.startswith(_PREFIX_DPAPI) or text.startswith(_PREFIX_AES)
