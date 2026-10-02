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

## 第二步：CI ✅

`.github/workflows/deploy.yml`（跟踪 `deploy/main`，构建 → 容器冒烟 → 推 GHCR →
SSH 部署）+ `deploy/panwatch-deploy.sh`（带回滚的部署脚本）。

### stock-agent 当时在此步的阻塞复盘（本工作流的防堵设计依据）

从 stock-agent 仓库提交历史还原（`491ab05`→`688d282`→`5b9cce7`→`8256941`
连续 4 个提交在救火）：

1. **GHCR 私有包拉取认证**：服务器 `docker pull ghcr.io/...` 前必须
   `docker login`。修法：CI 把本次工作流的短期 `GITHUB_TOKEN` 经 stdin 传给
   服务器 docker login，拉完即 logout，token 不落盘。
2. **部署用户 sudo 授权**：`sudo docker login && sudo bash xx.sh` 逐命令 sudo
   失败（sudoers 未放行/需要密码），最终修法是服务器 sudoers 给部署用户放行
   `/usr/bin/bash`，CI 统一 `sudo /usr/bin/bash -c '...'` 执行。
3. **反馈回路过长**：每次试错都要先跑完 ~10 分钟构建才在最后一步暴露部署
   失败，连续红灯。PanWatch 增加 preflight job（秒级）在构建前验证
   SSH / `sudo -n /usr/bin/bash` / docker / ghcr.io 可达性。

PanWatch 侧新增的防堵措施：

- 所有 `ssh/scp` 统一带 `ConnectTimeout=10`、`BatchMode=yes`、
  `ServerAliveInterval=15`——网络黑洞时按超时失败，不挂满 job 时限。
- `concurrency: deploy-deploy-main`（`cancel-in-progress: false`）：连续 push
  时部署排队执行，不并发互踩，也不打断进行中的部署。
- 各 job 设 `timeout-minutes`（preflight 3 / build 30 / deploy 15）。
- 部署脚本：compose/env 缺失立刻报“第三步未完成”；健康等待有界
  （默认 60×5s，首启要下载 Chromium）；失败经 trap 回滚上一镜像并输出
  `docker logs`。
- **`DEPLOY_ENABLED` 仓库 Variable 开关**（默认未设置）：未开启时只做
  构建+冒烟+推送 GHCR，deploy job 直接跳过——先把 CI 链路跑绿，服务器侧
  （第三步）就绪后再设 `DEPLOY_ENABLED=true` 打开部署，避免 stock-agent
  式的部署红灯迭代。

### 启用部署前的仓库/服务器前置条件（2026-10-02 已完成 ✅）

仓库 Settings（已由 Agent 经 gh CLI 执行完毕）：

1. Environments → `production`：已创建，**未配置** required reviewers
   （配置了会导致 deploy job 静默等待人工批准——阻塞源）。
2. Secrets（`production` Environment）：`DEPLOY_HOST`、`DEPLOY_PORT`、
   `DEPLOY_USER`、`DEPLOY_SSH_PRIVATE_KEY`、`DEPLOY_KNOWN_HOSTS` 已全部写入。
   复用 stock-agent 时代的部署密钥（`~/.ssh/stock-agent-production`，服务器
   authorized_keys 已信任，host 指纹经 keyscan 与
   `Stock-Agent docs/deployment/server-access.md` 记录比对一致）。
3. Repository variables：`DEPLOY_ENABLED` **暂未设置**（第三步就绪后再设
   `true`，此前的推送只跑构建+冒烟+推 GHCR，deploy job 自动跳过）。

服务器：无需新配置。部署用户沿用 `stockagent`（uid 999），其
`sudo /usr/bin/bash` NOPASSWD 授权 2026-09-01 已生效，deploy.yml 的
`sudo /usr/bin/bash -c '...'` 调用方式与之匹配。服务器现运行 CareerPass
业务，PanWatch 使用独立目录，互不干扰。

镜像标签策略与 stock-agent 一致：生产跑完整 commit SHA 镜像，
`stable` 仅用于人工拉取检查；回滚 = 改 `/etc/panwatch/image.env` 后重建。

手动触发：Actions → Deploy container → Run workflow（`DEPLOY_ENABLED=true`
后为完整构建+部署链路）。

## 第三步：服务器侧（待做）

compose 常驻服务：镜像 `ghcr.io/mingyuexinc/panwatch`、`mem_limit: 384m`、
数据卷挂 `/app/data`（DB + Playwright 浏览器 + 日志）、健康检查沿用镜像内
`/api/health`。

生产环境变量文件 `/etc/panwatch/panwatch.env`（权限 600，不入仓库）：

- `AUTH_USERNAME` / `AUTH_PASSWORD`：首启自动建号（`auth.py`
  `init_auth_from_env`，一次性播种，之后以 DB 为准）。**值已于 2026-10-02
  裁定**（见会话记录 / 服务器 env 文件），公网可登录、前端监控池看板依赖
  此账号。注意：env 播种路径不受网页端 6 位密码下限约束，改密走前端设置页。
- `JWT_SECRET`（可选）：不设则首启自动生成并存入 DB（数据卷内，重启不变）。

Caddy 站点块要求：**独立站点（子域名或根路径），不要挂 `/panwatch/` 之类
子路径**——vite 产物资源为绝对路径（`/assets/...`），子路径会 404；SPA
fallback（非 API 路径回 `index.html`）后端已内置。反代到宿主机
`127.0.0.1:8000`。

> 内存提示：首次启动会向 `/app/data/playwright` 下载 Chromium（约 170MB 磁盘），
> 内存峰值超出 384m 时可设 `PLAYWRIGHT_SKIP_BROWSER_INSTALL=1` 放弃截图功能。
