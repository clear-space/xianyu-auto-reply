"""
Scheduler服务配置模块

功能：
1. 继承common配置基类
2. 添加Scheduler服务特定配置
3. 从.env文件读取配置
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from common.core.config import BaseConfig


class SchedulerConfig(BaseConfig):
    """
    Scheduler服务配置类
    
    包含Scheduler服务特定配置：
    - 服务端口
    - 服务间通信URL
    """

    # 服务配置
    project_name: str = Field(default="Xianyu Scheduler Service")
    service_port: int = Field(default=8091, alias="SCHEDULER_PORT")
    
    # 服务间通信URL
    websocket_service_url: str = Field(
        default="http://127.0.0.1:8090",
        alias="WEBSOCKET_SERVICE_URL"
    )
    backend_web_service_url: str = Field(
        default="http://127.0.0.1:8089",
        alias="BACKEND_WEB_SERVICE_URL",
    )

    @property
    def backend_service_url(self) -> str:
        """兼容旧字段名（scheduled_publish_task / offline_task 仍在使用）。"""
        return self.backend_web_service_url


@lru_cache
def get_settings() -> SchedulerConfig:
    """返回缓存的配置实例"""
    return SchedulerConfig()
