# PanWatch 部署（deploy/main 分支）

部署三件套的落地记录。上游 `Dockerfile` 保持不动（便于合并 upstream），
部署专用文件全部收敛在 `deploy/` 与 `requirements-deploy.txt`。

## 第一步：精简镜像 ✅（2026-10-02 实测）

`deploy/Dockerfile` + `requirements-deploy.txt` + `deploy/Dockerfile.dockerignore`。

| 项目 | 上游 Dockerfile | deploy 版 | 说明 |
| --- | --- | --- | --- |
| 基础镜像 | python:3.11-slim-bookworm | python:3.13-slim-bookworm | 仍锁 Bookworm（Trixie 的 libgbm1 会拉入 Mesa/LLVM） |
| 前端 | 镜像内 node 阶段构建 | CI/本地预构建，`COPY frontend/dist` | 无 node/pnpm 进镜像 |
| Python 依赖 | requirements-runtime.txt | requirements-deploy.txt（实测最小集） | 见下 |
| builder apt | git（装 TradingAgents） | 无 | |

依赖集差异（判定标准：全仓 grep 实际 import，含懒加载 + 容器冒烟通过）：

- **移除 tradingagents**（git 安装，连带 langchain/langgraph 等约 115 包，体积大头）。
  代码懒加载，缺失时 Agent 显示“未安装”并优雅降级（`_check_availability`）。
- **移除 efinance / tenacity / pyyaml**（零 import；efinance 仅存在于注释）。
- **移除 xhtml2pdf**（仅 WeasyPrint 缺系统库时的 PDF 回退；镜像装有 pango/cairo +
  中文字体，主引擎必可用；连带省掉 reportlab/pypdf）。
- 显式补 `markdown` / `requests`（app 直接 import，此前靠 apprise 传递安装）。
- 实测不可再摘：`py_mini_racer`(48M) / `curl_cffi`(38M) 被 `import akshare`
  顶层硬引用，卸掉即 ModuleNotFoundError。

实测结果（Windows 本机 Docker Desktop，linux/amd64）：

- **压缩体积 263MB**（`docker save | gzip -1` ≈ GHCR 拉取体积，命中 250~300MB 目标）
- 解压体积 1.03GB（`docker images`）。
  大头：python 依赖 477M（playwright 130 + pandas/numpy 98 + py_mini_racer 48 +
  curl_cffi 38 + fontTools 23）、apt 131M（fonts-noto-cjk + Playwright X 库）、
  python:3.13-slim 基础 142M。
- 冒烟（`scripts/docker_smoke_deploy.py`）：本地包导入 / WeasyPrint 中文 PDF /
  Chromium 截图（中文字体 + canvas）/ HTTP 启动 + 健康检查 + 前端 —— 全部 PASS。
  Python 3.13 全栈兼容性由此覆盖。

本地构建：

```bash
make build-deploy VERSION=0.3.0        # = pnpm --dir frontend build + docker build -f deploy/Dockerfile
docker run --rm -v "$PWD:/checks:ro" panwatch:deploy python /checks/scripts/docker_smoke_deploy.py
```

注意：`deploy/Dockerfile.dockerignore` 存在时**整体取代**根 `.dockerignore`
（BuildKit 规则），已放行 `frontend/dist` 并保留 `packages/*/README.md`
（wheel 构建需要）。

## 第二步：CI（待做）

借鉴 Stock-Agent 的 `container.yml`：Actions 构建 → 推 GHCR → SSH 部署，
跟踪 `deploy/main`。构建前先跑 `pnpm --dir frontend build` 产出 dist，
再 `docker build -f deploy/Dockerfile`。冒烟用 `scripts/docker_smoke_deploy.py`。

## 第三步：服务器侧（待做）

compose 常驻服务：镜像 `ghcr.io/mingyuexinc/panwatch`、`mem_limit: 384m`、
数据卷挂 `/app/data`（DB + Playwright 浏览器 + 日志）、健康检查沿用镜像内
`/api/health`。Caddy 站点块反代 `localhost:8000`。

> 内存提示：首次启动会向 `/app/data/playwright` 下载 Chromium（约 170MB 磁盘），
> 内存峰值超出 384m 时可设 `PLAYWRIGHT_SKIP_BROWSER_INSTALL=1` 放弃截图功能。
