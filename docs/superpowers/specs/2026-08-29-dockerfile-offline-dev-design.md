# Dockerfile.offline-dev 设计 — 离线开发环境镜像

## 目标

提供一个**单镜像离线开发环境**，满足：

1. 整个项目打包进镜像，构建期完成前后端依赖安装；
2. 前后端依赖**缓存目录保留在镜像中**（不删除），支持离线 `install/build`；
3. 支持挂载本地源码（用户挂载后自行利用镜像内缓存做离线重装）。

使用方式已确认：**纯开发环境容器**（`docker run -it` 进入交互 shell，容器内
`make dev` 起服务，体验与有网本地开发一致），
并内置 `vim`、`git` 等基本开发工具。

## 方案选型

采用**单阶段 Debian 镜像**（`python:3.12-bookworm` 完整版）：

- 完整版自带 gcc/make 等编译链，离线环境下仍可构建 Python 原生扩展、处理临时依赖变更；
- 不拆分 builder/runtime stage —— 这是开发容器，不需要精简产物；
- Node.js 22 通过 nodesource 安装（与 `backend/Dockerfile` 现有做法一致）；
- uv 通过 `COPY --from=ghcr.io/astral-sh/uv:latest`（可用 `UV_IMAGE` build arg 覆盖，受限网络可用内部镜像）；
- pnpm 通过 corepack 安装，版本固定为 10.26.2（与 `frontend/Dockerfile`、`pnpm-workspace.yaml` 一致）。

被否选的方案：复用现有两个 Dockerfile 做 multi-stage 拼装（复杂且不适合开发容器）；
slim 基础镜像 + 手动装编译链（离线可维护性差）。

## 镜像内容

- **基础系统**：`python:3.12-bookworm`，`apt` 安装 `git vim curl ca-certificates jq less procps`（开发常用工具）；
  支持 `APT_MIRROR` build arg（复用 backend/Dockerfile 的镜像源归一化逻辑，受限网络构建用）。
- **Node.js 22 + pnpm 10.26.2**（corepack），支持 `NPM_REGISTRY` build arg。
- **uv**（含 `uvx`），经 `ARG UV_IMAGE` + `FROM ... AS uv-source` 阶段 COPY 进镜像。
- **docker CLI**：经 `ARG DOCKER_CLI_IMAGE=docker:cli` + `FROM ... AS docker-cli` 阶段
  `COPY --from=docker-cli /usr/local/bin/docker`，支持 AIO 沙箱本地（DooD）模式。
- **provisioner 独立 venv** `/opt/provisioner-venv`：`uv venv` + `uv pip install
  fastapi "uvicorn[standard]" kubernetes`，支持 AIO 沙箱 K3s/provisioner 模式。
  必须用独立 venv 而非 `backend/.venv`——挂载本地源码后 `uv sync` 会按 lockfile
  裁剪掉非锁定包。
- **nginx**：apt 安装（`make dev` 的 serve.sh 原生流程直接使用
  `docker/nginx/nginx.local.conf`，无需镜像内再派生配置）。
- **lsof + iproute2(ss)**：serve.sh 的端口健康检查/进程收割依赖，缺了会导致
  `make dev` 启动失败。
- **全局 Python 常用库**：`uv pip install --python /usr/local/bin/python3
  numpy pandas matplotlib scipy pillow requests beautifulsoup4 lxml openpyxl
  scikit-learn pymupdf pypdf pdfplumber reportlab`（含 PDF 四件套：
  PyMuPDF 解析/渲染、pypdf 合并拆分、pdfplumber 表格抽取、reportlab 生成）。
  本地沙箱（LocalSandboxProvider + allow_host_bash）下 agent 的
  bash 工具用系统 Python（与 backend/.venv 独立），库必须装全局；用 uv pip 让
  wheel 落进 /root/.cache/uv 随镜像层保留。构建期预热 matplotlib 字体缓存
  （Agg 后端）。
- **opencode + DCP 插件**：`npm install -g opencode-ai`，并复现
  `opencode plugin @tarquinen/opencode-dcp@latest --global` 的终态（该 CLI 带
  交互 spinner，构建期非 TTY 下不可靠）——全局配置
  `/root/.config/opencode/opencode.json` 写入 plugin 列表；插件包用
  `npm install --save-exact` 预装进 `/root/.cache/opencode/packages/
  @tarquinen/opencode-dcp@latest/`（package.json 精确锁版本 + package-lock.json
  + node_modules）。已实测：未预置缓存时 opencode 离线启动卡在
  "Installing plugin package"；预置后 bootstrap 直接通过。
- **tiktoken BPE 缓存**：`ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache` + 构建期用
  backend/.venv 预下载 `cl100k_base`（项目唯一使用的编码，
  `deerflow/agents/memory/prompt.py`）。否则离线时 `memory.token_counting:
  tiktoken`（默认值）的 warm-up 会因下载 BPE 数据失败而回退 char 估算
  （app/gateway/app.py），精度受损。缓存键是 URL 的 SHA1，命中即不再联网。
- **整个项目源码** `COPY . /app/general_agent`（见下文 dockerignore）。
- **后端依赖**：`cd backend && uv sync --all-packages --all-extras`（构建期联网执行）。
  `--all-extras` 默认装全部 optional extras（backend 根 `postgres`/`discord`，
  harness `ollama`/`postgres`/`phoenix`/`pymupdf`/`tui`/`groundroute`）——离线环境下
  运行时 `uv sync --extra X` 无法拉取，必须构建期烘入；用户离线重装时也必须带
  `--all-extras`，否则 uv sync 会把预装 extras 裁剪掉。
  额外 `uv pip install --no-deps hatchling` 把构建后端烘进 uv 缓存
  （复用 backend/Dockerfile 的做法，保证离线 `uv sync --offline` 能解析 `[build-system].requires`）。
- **前端依赖**：`cd frontend && pnpm install --frozen-lockfile`。
- **离线默认环境变量**（ENV 在所有构建期 RUN 之后设置）：
  - `UV_OFFLINE=1`——serve.sh 的 `uv sync` 不带 `--offline`，uv 会按 HTTP
    cache-control TTL **重校验 registry 元数据**（如解析 deerflow-harness 的
    `build-system.requires: hatchling`），离线 DNS 失败即硬报错，即使缓存完整。
    "联网跑过一次后离线正常"只是缓存暂时新鲜，TTL 过后复现。UV_OFFLINE=1 让
    uv 默认只用缓存、永不联网；故意联网时 `docker run -e UV_OFFLINE=0` 覆盖。
  - `UV_INDEX_URL=<构建期同值>`——uv 的 registry 元数据缓存按 index URL 分桶，
    运行时用别的 index 会缓存 miss，故烘为构建期值且运行时勿覆盖。

## 缓存保留（核心要求 2）

镜像中**显式保留**以下缓存目录，任何清理步骤都不得触及：

| 路径 | 用途 |
|---|---|
| `/root/.cache/uv` | uv 包缓存 — 离线 `uv sync --frozen --offline` 可用 |
| `/root/.local/share/pnpm/store` | pnpm 内容寻址 store — 离线 `pnpm install --offline` 可用 |
| `backend/.venv` | 预装的后端虚拟环境 |
| `frontend/node_modules` | 预装的前端依赖 |

不使用 `RUN --mount=type=cache`（BuildKit 临时缓存不进镜像层），改为普通 `RUN` 让缓存
落进镜像层。`apt` 缓存可正常清理（离线环境本就无法 `apt install` 新包，不属于前后端依赖缓存）。

## Dockerignore（关键问题）

根目录现有 `.dockerignore` 排除了 `tests/`、`docs/`、`scripts/`、`skills/`、`*.md` 等
（因为现有 Dockerfile 只需要 backend/frontend）。为满足"整个项目打包"，新增
**`Dockerfile.offline-dev.dockerignore`**（Docker 自动识别与 Dockerfile 同名的
`<name>.dockerignore`），只排除：

```
.git
.env
.dockerignore
**/__pycache__
**/.venv
**/node_modules
**/.next
logs/
backend/.deer-flow
*.log
config.yaml
extensions_config.json
```

`config.yaml` / `extensions_config.json` 是本地真实配置（可能含 API key），
**绝不烘进镜像**；容器内用 `make config` 从模板重新生成（挂载源码场景下
宿主机的配置直接生效）。

保留 `tests/`、`docs/`、`scripts/`、`skills/`、`config.example.yaml`、
`extensions_config.example.json` 等完整开发所需内容。

## 使用方式

```bash
# 构建（构建期需要网络）
docker build -f Dockerfile.offline-dev -t deer-flow-offline-dev .

# 离线环境运行 — 与有网本地开发同体验：容器内 make dev
docker run -it --rm -p 2026:2026 -p 8001:8001 -p 3000:3000 deer-flow-offline-dev
cd /app/general_agent && make config && vim config.yaml && make dev
# 浏览器访问 http://localhost:2026（serve.sh 原生起 Gateway+Frontend+Nginx）

# 挂载本地源码（镜像内依赖被遮盖后，利用保留的缓存离线重装，然后 make dev）
docker run -it --rm -v $(pwd):/app/general_agent deer-flow-offline-dev
cd /app/general_agent/backend && uv sync --all-packages --all-extras --frozen --offline
cd /app/general_agent/frontend && pnpm install --frozen-lockfile --offline
cd /app/general_agent && make dev
```

不内置启动脚本/entrypoint（YAGNI：serve.sh 就是现成的编排，容器内直接
`make dev` 即可获得与本地开发完全一致的热重载、统一日志、Ctrl+C 全停体验）。
LLM 服务在宿主机时 config.yaml 的 base_url 用 `host.docker.internal`（Linux 需
`--add-host host.docker.internal:host-gateway`）。
AIO 沙箱调试的完整命令（DooD / K3s 两种模式）见
[tutorials/offline_development.md](../../../tutorials/offline_development.md) 第 2 章。

## 交付物

1. `Dockerfile.offline-dev`（仓库根目录）
2. `Dockerfile.offline-dev.dockerignore`（仓库根目录）
3. `tutorials/offline_development.md` 重写为两章：`## 1. 本地开发`（宿主机原生开发，
   归纳原文档，去对比表格）+ `## 2. 容器开发`（本镜像用法 + AIO 沙箱 DooD/K3s 调试）
4. 根 `AGENTS.md` / `README.md` 补充说明；README 小节瘦身并链接教程

## 验证

- `docker build` 成功（本机有网可验证）；
- 容器内 `uv sync --frozen --offline`、`pnpm install --offline` 在无代理环境下成功
  （可用 `docker run --network none` 模拟离线验证）；
- 挂载本地源码后离线重装成功。
