# PanWatch 部署镜像(deploy 专用,不影响上游 Dockerfile)
#
# 与上游 Dockerfile 的差异(目标 250~300MB,实测见 deploy/README.md):
# 1. 基础镜像 python:3.13-slim-bookworm(上游 3.11)。
#    仍固定 Bookworm:Trixie 的 libgbm1 会强制引入 Mesa/LLVM 图形驱动栈。
# 2. 无 node 构建阶段:前端 dist 由 CI(或本地 pnpm build)预先构建,
#    经构建上下文 frontend/dist 直接 COPY,镜像内不装 node/pnpm。
#    (依赖 deploy/Dockerfile.dockerignore 放行 frontend/dist,
#    它会整体取代根目录 .dockerignore)
# 3. 依赖装 requirements-deploy.txt 实测最小集:无 tradingagents
#    (省掉 git 阶段与 langchain 全家桶)、无 xhtml2pdf/reportlab 等。
#    Agent 懒加载,缺失时界面显示"未安装"并优雅降级。
#
# 本地构建(需先构建前端 dist):
#   corepack pnpm --dir frontend build
#   docker build -f deploy/Dockerfile --build-arg VERSION=$(cat VERSION) -t panwatch:deploy .

# ===== Stage 1: Python 依赖构建 =====
# 构建与运行同版本,保证原生 wheel ABI 一致。最小依赖集无需 git,不装 apt 包。
FROM python:3.13-slim-bookworm AS python-builder

WORKDIR /app

COPY requirements-deploy.txt ./
COPY packages/ ./packages/

# 镜像内安装正常包(非 editable);不预编译字节码,避免 .pyc 进镜像。
RUN sed 's/^-e //' requirements-deploy.txt > /tmp/requirements-deploy.txt \
    && pip install --no-cache-dir --no-compile --prefix=/install -r /tmp/requirements-deploy.txt


# ===== Stage 2: Python 运行环境 =====
FROM python:3.13-slim-bookworm

# 版本号(构建时传入)
ARG VERSION=dev

WORKDIR /app

# 系统依赖(与上游镜像一致,已在 #168 实测收敛):
# - tzdata: 时区数据(zoneinfo 模块需要)
# - fonts-noto-cjk: 中文字体(K线截图/PDF 导出)
# - libpangoft2/libpangocairo: WeasyPrint PDF 排版
# - 其余为 Playwright 1.57 的 Debian 12 Chromium 依赖(无头截图):
#   https://github.com/microsoft/playwright/blob/v1.57.0/packages/playwright-core/src/server/registry/nativeDeps.ts
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    fonts-noto-cjk \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libx11-6 \
    libxcb1 \
    libxext6 \
    libfontconfig1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 仅复制安装产物,构建工具留在 builder。
COPY --from=python-builder /install/ /usr/local/

# 复制后端代码
COPY src/ ./src/
COPY server.py ./
COPY prompts/ ./prompts/

# 写入版本号
RUN echo "${VERSION}" > VERSION

# 前端静态文件:由 CI/本地预先构建(本 Dockerfile 无 node 阶段)
COPY frontend/dist/ ./static/

# 创建数据目录(挂载点:Playwright 浏览器/数据库/日志均落此处)
RUN mkdir -p /app/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DATA_DIR=/app/data
ENV DOCKER=1

# 默认时区(docker run -e TZ=... 可覆盖)
ENV TZ=Asia/Shanghai

EXPOSE 8000

# start-period 给首启下载 Chromium(~170MB 到 /app/data/playwright)留宽限,
# 期间探测失败不计入 retries;就绪后首个成功探测立即转 healthy。
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 启动命令
CMD ["python", "server.py"]
