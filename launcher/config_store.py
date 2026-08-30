"""
配置持久化模块

功能：
1. 保存用户填写的MySQL和Redis连接信息到本地文件
2. 下次启动时自动加载已保存的配置
3. 密码字段加密存储（Windows DPAPI / 其它平台 AES-256-GCM，见 secret_crypto.py），
   防止明文泄露密码
4. 兼容旧版 Base64 编码的历史配置文件，首次加载成功后自动迁移为密文
"""

import json
from pathlib import Path

from launcher.secret_crypto import decrypt_secret, encrypt_secret, is_encrypted_secret

# 配置文件名
_CONFIG_FILE = "connection.dat"

# 配置文件格式版本标记：旧版（无此字段）密码为 Base64 编码，新版为密文
_ENC_VERSION = 2


def _get_config_path() -> Path:
    """
    获取配置文件路径

    Returns:
        配置文件的完整路径
    """
    from launcher.frozen_detect import get_project_root
    base_dir = get_project_root()
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _CONFIG_FILE


def _load_raw_data() -> dict | None:
    """读取配置文件原始 JSON 内容（不解析密码）。"""
    config_path = _get_config_path()
    if not config_path.exists():
        return None
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_connection_config(config: dict) -> bool:
    """
    保存连接配置到文件

    对敏感字段（密码）做加密后保存（Windows DPAPI / 其它平台 AES-GCM）。

    Args:
        config: 连接配置字典，包含mysql和redis的连接信息
    Returns:
        True保存成功，False保存失败
    """
    try:
        save_data = {
            "enc_version": _ENC_VERSION,
            "mysql_host": config.get("mysql_host", ""),
            "mysql_port": str(config.get("mysql_port", "3306")),
            "mysql_user": config.get("mysql_user", ""),
            "mysql_password": encrypt_secret(str(config.get("mysql_password", ""))),
            "mysql_database": config.get("mysql_database", ""),
            "redis_host": config.get("redis_host", ""),
            "redis_port": str(config.get("redis_port", "6379")),
            "redis_password": encrypt_secret(str(config.get("redis_password", ""))),
            "redis_db": str(config.get("redis_db", "0")),
        }
        config_path = _get_config_path()
        config_path.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_connection_config() -> dict | None:
    """
    从文件加载连接配置

    Returns:
        配置字典，如果文件不存在或读取失败返回None。
        密码解密失败时对应字段返回空字符串（需用户重新填写密码）。
    """
    data = _load_raw_data()
    if data is None:
        return None

    def _decode_password(field: str) -> str:
        try:
            return decrypt_secret(str(data.get(field, "")))
        except Exception:
            # 密文无法解密（如非 Windows 平台机器码变化 / DPAPI 用户变化），
            # 返回空字符串让用户在配置页重新填写密码
            return ""

    config = {
        "mysql_host": data.get("mysql_host", ""),
        "mysql_port": data.get("mysql_port", "3306"),
        "mysql_user": data.get("mysql_user", ""),
        "mysql_password": _decode_password("mysql_password"),
        "mysql_database": data.get("mysql_database", ""),
        "redis_host": data.get("redis_host", ""),
        "redis_port": data.get("redis_port", "6379"),
        "redis_password": _decode_password("redis_password"),
        "redis_db": data.get("redis_db", "0"),
    }

    # 旧版文件（Base64 编码密码）自动迁移为密文：
    # 密码字段非密文格式且解密成功时，重新保存一次完成升级
    need_migrate = (
        int(data.get("enc_version", 1)) < _ENC_VERSION
        and not is_encrypted_secret(str(data.get("mysql_password", "")))
    )
    if need_migrate:
        save_connection_config(config)

    return config
