# Backend-Web 服务 Dockerfile
# 单阶段构建，直接使用Python源码

# 基础镜像走 docker.io 官方名（由 buildx 的 buildkitd 镜像加速配置拉取，见流水线 BUILDKIT 配置）
FROM python:3.11-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 配置国内镜像源
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

RUN apt-get update && apt-get install -y curl ca-certificates gcc \
    fonts-dejavu-core fonts-liberation \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 复制项目源码
COPY common /app/common
COPY backend-web /app/backend-web
COPY launcher /app/launcher

# 从pyproject.toml提取并安装依赖
RUN python -c "\
import tomllib; \
deps = tomllib.load(open('/app/backend-web/pyproject.toml','rb')).get('project',{}).get('dependencies',[]); \
open('/tmp/requirements.txt','w').write('\n'.join(deps))" && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# 从本地 zip 安装 Chromium 到 playwright/patchright 缓存（避免 CDN 被墙）
COPY docker/chrome-linux64.zip /tmp/
COPY docker/install-local-chrome.py /tmp/
RUN python /tmp/install-local-chrome.py /tmp/chrome-linux64.zip --package playwright && \
    python /tmp/install-local-chrome.py /tmp/chrome-linux64.zip --package patchright && \
    rm /tmp/chrome-linux64.zip /tmp/install-local-chrome.py

# 安装系统依赖 + 验证浏览器（浏览器已从本地 zip 安装，跳过下载）
RUN python -m playwright install --with-deps chromium
RUN python -m patchright install --with-deps chromium

# 创建日志目录
RUN mkdir -p /app/backend-web/logs /app/static/uploads

# 暴露端口
EXPOSE 8089

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV TZ=Asia/Shanghai

# 启动服务
CMD ["python", "backend-web/main.py"]
