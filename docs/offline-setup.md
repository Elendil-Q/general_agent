# DeerFlow 离线安装与调试指南（Docker 全栈）

本指南说明如何在**完全气隙（无外网）**的目标机器上，以 **Docker 全栈**方式部署并调试 DeerFlow。所有依赖与运行时（nginx、pnpm、node、uv、python）均运行在容器内，宿主机零安装、零冲突。

部署分为**生产模式**与**开发模式**两节，分别按步骤介绍。开发模式支持源码挂载与热重载，可在离线环境下直接修改代码并即时生效。

---

## 0. 概述与前提

### 适用场景

- 源机器（联网）预构建镜像 → 导出 → 传输 → 目标机器（Air-gapped）加载运行。
- 目标机已具备本地 LLM（Ollama 或 vLLM），模型权重就绪（本指南不打包模型）。
- 宿主机仅安装 Docker，不安装 nginx、node、pnpm、uv、python。

### 两种部署模式对比

| 维度 | 生产部署 | 开发部署 |
| --- | --- | --- |
| 镜像 | 后端 `runtime` target，前端 `prod` target | 后端 `dev` target，前端 `dev` target |
| 源码挂载 | 否（镜像内为预构建产物） | **是**（宿主机 `backend/`、`frontend/src/` 等挂载到容器） |
| 热重载 | 无 | **有**（后端 uvicorn `--reload`，前端 Next.js Turbopack） |
| 运行时联网 | 零（`--no-sync` + 预构建） | 依赖已预装，通常零联网；启动时 `uv sync` 做秒级本地校验 |
| 启动命令 | `scripts/deploy.sh start` | 手动 `docker compose up -d`（**不带 `--build`**） |
| 推荐场景 | 离线运行、演示、稳定环境 | **离线调试、改源码、迭代开发** |
| 改代码后更新 | 源机重建镜像 → 重新传输 → 加载 | 宿主机直接改源码，容器即时热重载 |

### 离线可行性结论

| 组件 | 离线可行性 |
| --- | --- |
| 存储 | 默认 **SQLite**，落在宿主机 `$REPO/backend/.deer-flow`（bind mount），容器删除数据不丢 |
| Agent 运行时 | Gateway **内嵌** LangGraph 运行时，进程内运行，无外部服务依赖 |
| LLM | 指向宿主机本地端点，容器经 `host.docker.internal` 访问 |
| MCP 服务器 | `extensions_config.example.json` 默认 `enabled: false`，保持禁用即可 |
| nginx/pnpm/node/uv/python | 全部在容器内，宿主机零安装 |

### 关键前提（务必先确认）

1. **源机与目标机 CPU 架构必须一致**（`uname -m` 相同，如均为 `x86_64` 或均为 `aarch64`）。若不一致，源机必须**跨架构构建**镜像——否则镜像里的原生二进制（前端的 `@esbuild/*`、`@next/swc-*`、`@tailwindcss/oxide-*`；后端的 native wheel）架构不符，在目标机会以 `exec format error` 崩溃。方向对照：
   - 源机 x86_64 → 目标机 arm64（`aarch64`）：构建时用 `--platform linux/arm64`
   - 源机 arm64（如 Mac M 系列）→ 目标机 x86_64：构建时用 `--platform linux/amd64`
   - 跨架构构建的具体命令（含 binfmt 注册）见 §1.2 / §1.3 的"跨架构构建"小节。
2. **目标机已装 Docker**（含 `docker compose` 插件）。宿主机唯一需要的工具。
3. **目标机本地 LLM 已就绪**（Ollama 监听 `:11434` 或 vLLM 监听 `:8000`）。
4. 传输介质：U 盘或内网（目标机可访问）。

### 端口规划与自定义（避免端口冲突）

四个端口全部集中在 `.env`，改端口**只动 `.env` 一个文件**，自动同步到 `docker-compose.yaml`、`docker-compose-dev.yaml`、`docker/dev-entrypoint.sh`、`scripts/serve.sh`、`nginx.conf`。**无需重建镜像，无需手改 compose / nginx**。

#### `.env` 变量与影响范围

| 变量 | 默认 | 影响的服务 | 自动同步到 |
|---|---|---|---|
| `PORT` | `2026` | nginx 对外入口 | compose nginx `ports`；`deploy.sh` 启动提示；本地 `serve.sh` nginx listen |
| `GATEWAY_PORT` | `8001` | gateway uvicorn | compose gateway `command` / `dev-entrypoint.sh`；nginx.conf upstream；frontend env `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL`；gateway env `DEER_FLOW_CHANNELS_*_URL` |
| `FRONTEND_PORT` | `3000` | frontend next dev/start | compose frontend `PORT` env；nginx.conf upstream；本地 `serve.sh` frontend 命令 |
| `PROVISIONER_PORT` | `8002` | provisioner | nginx.conf upstream（仅 provisioner/K8s 模式） |

示例（对外 `2027`、gateway `8010`、frontend `3001`）：

```bash
# .env
PORT=2027
GATEWAY_PORT=8010
FRONTEND_PORT=3001
```

所有引用点（nginx upstream、channel URLs、frontend 内部网关地址）自动跟上。**勿把 `GATEWAY_PORT` 设成 `8002`**（provisioner 占用）。不设任何变量 = 用默认 `2026/8001/3000/8002`，行为与原版一致。

#### 生产部署

`PORT` = 宿主机映射端口，容器内 nginx 仍 `listen 2026`，靠 `${PORT:-2026}:2026` 映射。改完 `.env` 后：

```bash
scripts/deploy.sh start      # 自动读 .env 的 PORT
```

#### 开发部署（Docker）

`PORT` 同生产（宿主机映射）。dev 模式 gateway 端口在 `dev-entrypoint.sh` 里读 `GATEWAY_PORT`（容器 env 经 `env_file: ../.env` 注入）。改完 `.env` 后：

```bash
cd docker
docker compose -p deer-flow-dev -f docker-compose-dev.yaml up -d frontend gateway nginx
# 若改了 GATEWAY_PORT，须 restart gateway 让 dev-entrypoint 重新读取：
docker compose -p deer-flow-dev -f docker-compose-dev.yaml restart gateway
```

> `dev-entrypoint.sh` 是 ro mount，`.env` 改完容器内即时可见，但脚本只在启动时执行一次，故改 `GATEWAY_PORT` 后须 `restart gateway`。

#### 本地开发（`make dev`，非 Docker）

`PORT` 语义不同：nginx 直接 `listen` 该端口（无映射层），`serve.sh` 用 sed 把 `__NGINX_PORT__` 写进临时 `temp/nginx.local.conf`。`GATEWAY_PORT` / `FRONTEND_PORT` 分别给 uvicorn / `next dev`。改完 `.env` 后重新 `make dev` 即生效（`serve.sh` 每次启动读 `.env`）。

### 已知离线陷阱（两种模式共用）

- **tiktoken**：默认 `memory.token_counting: tiktoken` 首次会从 OpenAI CDN 下载编码（issues #3402 / #3429）。离线须改为 `char`（见 §1.1）。
- **Ollama 的 extra**：`langchain-ollama` 是 `deerflow-harness` 的 `ollama` extra。两种 compose 均已将 `UV_EXTRAS` 传入 `backend/Dockerfile`，构建时 `uv sync --extra ollama` 即装入镜像（生产 `docker-compose.yaml:73`，开发 `docker-compose-dev.yaml`）。**在 `.env` 设 `UV_EXTRAS=ollama` 即可在两种模式下使用 Ollama**；构建后用 `uv pip list | grep -i ollama` 验证。vLLM 走内置 provider、无需 extra——二者并列可选，按本机已有的推理服务挑一个即可。
- **启动时带 `--build`**：`make up`、`make docker-start`、`scripts/docker.sh start`、`scripts/deploy.sh` 默认（不带参数）均会触发 `--build`，在气隙机将因联网失败。**离线启动时务必使用本指南指定的无 `--build` 命令**。

---

## 1. 源机器准备（通用步骤）

> 以下命令在源机器执行，`$REPO` 指向仓库根目录。

### 1.1 编辑配置文件（生产与开发共用）

从示例复制并编辑以下文件，放置于 `$REPO` 根目录。这些文件会随 `repo/` 目录一起传输到目标机。

**`config.yaml`**（从 `config.example.yaml` 复制，两种模式共用同一配置）：

```yaml
# vLLM（内置 provider，无需 extra）
models:
  - name: qwen3-vllm
    display_name: Qwen3 (vLLM)
    use: deerflow.models.vllm_provider:VllmChatModel
    model: Qwen/Qwen3-32B
    api_key: dummy
    base_url: http://host.docker.internal:8000/v1   # 宿主机 vLLM
    request_timeout: 600.0
    max_retries: 2
    max_tokens: 8192
    supports_thinking: true
    supports_vision: false
    when_thinking_enabled:
      extra_body:
        chat_template_kwargs:
          enable_thinking: true
```

```yaml
# Ollama（设 UV_EXTRAS=ollama，构建时装入镜像）
models:
  - name: qwen3-local
    display_name: Qwen3 (Ollama)
    use: langchain_ollama:ChatOllama
    model: qwen3:32b
    base_url: http://host.docker.internal:11434      # 宿主机 Ollama，不带 /v1
    num_predict: 8192
    temperature: 0.7
    reasoning: true
    supports_thinking: true
    supports_vision: false
```

> **base_url 用 `host.docker.internal`**：两个 compose 文件均已为 gateway 设置 `extra_hosts: "host.docker.internal:host-gateway"`，容器内可经此访问宿主机 LLM。

```yaml
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data

memory:
  enabled: true
  token_counting: char             # 避免 tiktoken 联网下载编码（#3402/#3429）

# 注释掉联网工具，保留本地工具
tools:
  # - name: web_search
  # - name: web_fetch
  # - name: image_search
  - name: ls
    group: file:read
    use: deerflow.sandbox.tools:ls_tool
  - name: read_file
    group: file:read
    use: deerflow.sandbox.tools:read_file_tool
  - name: glob
    group: file:read
    use: deerflow.sandbox.tools:glob_tool
  - name: grep
    group: file:read
    use: deerflow.sandbox.tools:grep_tool
  - name: write_file
    group: file:write
    use: deerflow.sandbox.tools:write_file_tool
  - name: str_replace
    group: file:write
    use: deerflow.sandbox.tools:str_replace_tool
  - name: bash
    group: bash
    use: deerflow.sandbox.tools:bash_tool

sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: true

run_events:
  backend: memory
```

**`extensions_config.json`**（从 `extensions_config.example.json` 复制）：默认所有 `mcpServers.*.enabled: false`，**保持禁用**。

**`.env`**（项目根，新建）：

```bash
# 用 Ollama 时填写 ollama；vLLM 时留空或删除
UV_EXTRAS=ollama

# 国内源机构建加速（可选，仅构建期生效）
# APT_MIRROR=mirrors.tuna.tsinghua.edu.cn   # 仅裸主机名；带 http:// 或 /debian 也会被自动清洗，非法值将 fail-fast
# NPM_REGISTRY=https://registry.npmmirror.com
# UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

> `.env` 中的 `UV_EXTRAS` 在生产与开发部署构建时均经各自 compose 文件传入 `backend/Dockerfile`（生产 `docker-compose.yaml`、开发 `docker-compose-dev.yaml`），无需手动 `--build-arg`。

### 1.2 构建并导出生产镜像（§2 生产部署专用）

> **跨架构构建**（源机与目标机架构不一致时，如 x86_64 源机 → arm64 目标机）：先注册目标架构模拟（`docker run --privileged --rm tonistiigi/binfmt --install arm64`，Docker Desktop 可跳过），再给下方构建命令加前缀：
> ```bash
> DOCKER_DEFAULT_PLATFORM=linux/arm64 scripts/deploy.sh build
> ```
> （arm64→x86 改用 `linux/amd64`）。生产 frontend（target=prod）的 `node_modules` 同样含原生二进制，必须跨架构构建。详见 §1.3 的"跨架构构建"小节。

```bash
cd "$REPO"

# 1. 构建生产镜像（backend target=runtime，frontend target=prod）
#    跨架构时前缀 DOCKER_DEFAULT_PLATFORM=linux/arm64（见上方引用块）
scripts/deploy.sh build

# 2. 验证 Ollama extra（仅 Ollama 用户；设了 UV_EXTRAS=ollama 即应有输出）
docker run --rm deer-flow-gateway uv pip list | grep -i ollama

# 3. 确认镜像存在
docker images | grep -E 'deer-flow-(gateway|frontend)|nginx:alpine'

# 4. 导出
docker save -o deer-flow-prod-images.tar \
  deer-flow-gateway \
  deer-flow-frontend \
  nginx:alpine

# 可选压缩
gzip deer-flow-prod-images.tar
```

> `nginx:alpine` 是 compose 直接 `image:` 引用，非构建产物，必须一并导出。`deer-flow-*` 镜像已自包含基础运行环境，无需再导出 `python:3.12-slim-bookworm` 等基础镜像。

### 1.3 构建并导出开发镜像（§3 开发部署专用）

#### 跨架构构建（源机与目标机架构不一致时，如 x86_64 源机 → arm64 目标机）

跨架构构建靠 buildx + QEMU 模拟。**frontend 和 gateway 都要跨架构**——frontend 的 `node_modules` 里有 `@esbuild/*`、`@next/swc-*`、`@tailwindcss/oxide-*` 等原生二进制，gateway 的 Python 也可能有 native wheel，架构不符会在目标机以 `exec format error` 崩溃。

1. **注册目标架构模拟**（Docker Desktop 自带，可跳过；Linux 源机须执行一次）：

   ```bash
   docker run --privileged --rm tonistiigi/binfmt --install arm64
   ```

2. **以目标架构构建**，二选一（下面命令里的 `linux/arm64` 换成你目标机的架构，arm64→x86 用 `linux/amd64`）：

   **方式 A（推荐，不改任何文件）**——给构建命令加环境变量前缀：

   ```bash
   cd "$REPO"
   DOCKER_DEFAULT_PLATFORM=linux/arm64 \
     docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml build frontend gateway
   ```

   **方式 B**——在 `docker/docker-compose-dev.yaml` 的 **frontend 和 gateway** 两个 service 顶层各加一行 `platform`：

   ```yaml
   frontend:
     platform: linux/arm64        # 顶层，与 build: 同级
     build: ...
   gateway:
     platform: linux/arm64
     build: ...
   ```

   > 方式 B 改的是受版本管理的 compose 文件，会影响所有用该文件构建的人；临时跨架构构建优先用方式 A，用完无需回改。

3. **基础镜像拉取**（若 `apt`/构建受网络限制但 `docker pull` 可达，或走了代理）——先手动拉取基础镜像再构建，避免构建中联网失败：

   ```bash
   docker pull python:3.12-slim-bookworm
   docker pull node:22-alpine
   docker pull ghcr.io/astral-sh/uv:0.7.20
   docker pull docker:cli
   ```

#### 构建与导出

```bash
cd "$REPO"

# 1. 构建前端 dev 镜像（target=dev，已装 node_modules）
#    跨架构时前缀 DOCKER_DEFAULT_PLATFORM=linux/arm64（见上方）
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml build frontend

# 2. 构建后端 dev 镜像（target=dev，已装 .venv；UV_EXTRAS 由 .env 经 compose 自动传入）
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml build gateway

# 3. 验证 Ollama extra（仅 Ollama 用户；设了 UV_EXTRAS=ollama 即应有输出）
docker run --rm deer-flow-dev-gateway uv pip list | grep -i ollama

# 4. 确认镜像
docker images | grep -E 'deer-flow-dev-(gateway|frontend)|nginx:alpine'

# 5. 导出
docker save -o deer-flow-dev-images.tar \
  deer-flow-dev-gateway \
  deer-flow-dev-frontend \
  nginx:alpine

# 可选压缩
gzip deer-flow-dev-images.tar
```

> 开发镜像含完整编译工具链（`build-essential`）与 `.venv`，用于支撑容器启动时的秒级 `uv sync` 校验及源码热重载。

### 1.4 准备干净的项目文件夹（离线本地开发用）

镜像构建完成后，把仓库整理成一份**干净、最小**的项目文件夹，作为离线机的本地开发目录（即 §1.5 传输内容里的 `repo/`）。核心原则：**镜像里已有的依赖（`backend/.venv`、`frontend/node_modules`）一律从这份文件夹删除**——它们是源机架构的产物，带到目标机既无用（容器用镜像里的，`gateway-venv` 还会覆盖 `.venv`），又浪费传输空间、还可能误导排查。

#### 保留（离线 dev 运行所需）

| 路径 | 用途 |
| --- | --- |
| `backend/`（除下方删除项） | 源码热重载，挂载到容器 `/app/backend/` |
| `frontend/src/`、`frontend/public/`、`frontend/next.config.js` | 前端源码热重载（compose 仅挂这三项） |
| `frontend/package.json`、`frontend/pnpm-lock.yaml`、`frontend/.env` | 依赖清单与前端环境变量 |
| `docker/`（`docker-compose-dev.yaml`、`nginx/nginx.conf`、`dev-entrypoint.sh`） | 目标机 `docker compose up` 必需 |
| `config.yaml`、`extensions_config.json`、`.env` | 运行配置 |
| `skills/` | 挂载到容器 |
| `Makefile`、`scripts/`、`docs/` | 工具与文档（可选，便于查阅） |

#### 删除（无用 / 可重建 / 错误架构）

| 路径 | 原因 |
| --- | --- |
| `backend/.venv/` | 源机架构虚拟环境；容器用镜像里的，`gateway-venv` 会覆盖 |
| `frontend/node_modules/` | 源机架构；容器用镜像里预装的 |
| `frontend/.next/` | 构建缓存，运行时重新生成 |
| `backend/.deer-flow/` | 运行时数据（SQLite、threads），目标机自建 |
| `logs/` | 运行时日志 |
| `**/__pycache__/`、`*.egg-info`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache` | Python 缓存，可重建 |
| `.git/` | 版本控制历史，离线开发通常不需要（保留则可本地 diff，但占空间） |
| `.claude/`、`.vscode/`、`.idea/` 等 | IDE/agent 配置，离线无关 |

#### 清理脚本（在副本上操作，不动源机工作环境）

```bash
cd "$REPO"
STAGE=/tmp/deer-flow-offline-repo
rm -rf "$STAGE"

# 复制时直接排除大块无用目录（源机架构依赖、git 历史、缓存、运行时数据）
rsync -a \
  --exclude='backend/.venv/' \
  --exclude='frontend/node_modules/' \
  --exclude='frontend/.next/' \
  --exclude='backend/.deer-flow/' \
  --exclude='logs/' \
  --exclude='.git/' \
  --exclude='.claude/' --exclude='.vscode/' --exclude='.idea/' \
  --exclude='__pycache__/' \
  --exclude='*.egg-info/' \
  --exclude='.pytest_cache/' --exclude='.ruff_cache/' --exclude='.mypy_cache/' \
  "$REPO"/ "$STAGE"/

# 兜底：删掉 rsync 可能遗漏的 __pycache__ / egg-info
find "$STAGE" -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +

echo "干净项目文件夹已生成：$STAGE"
du -sh "$STAGE"     # 预期比原仓库小很多（去掉了 .venv / node_modules / .git）
```

> **关于 `.git`**：脚本默认排除。若想在目标机用 `git diff` / `git log` 对照改动，去掉 `--exclude='.git/'`（会显著增大传输体积）。
>
> 清理后的 `$STAGE` 即 §1.5 传输内容里的 `repo/`。目标机加载镜像 + 拿到这份 `repo/` 即可按 §3 启动开发部署。生产部署（§2）理论上只需 `config.yaml`/`extensions_config.json`/`.env`，但带上 `repo/` 便于查阅与改配置。

### 1.5 传输内容

将 §1.4 准备好的 `repo/`（即 `$STAGE`）与镜像 tar 一起打包，传输到目标机：

```
offline-bundle/
├── deer-flow-prod-images.tar(.gz)   # 可选：若走生产部署
├── deer-flow-dev-images.tar(.gz)    # 可选：若走开发部署
└── repo/                              # §1.4 清理后的项目文件夹（含已编辑的 config.yaml / extensions_config.json / .env）
```

> **开发部署必须带上完整 `repo/` 源码**，因为容器会挂载宿主机源码目录进行热重载。生产部署理论上只需配置文件，但建议带上以便查阅与改配置。

---

## 2. 生产部署（步骤详解）

生产模式：镜像内为预构建产物，运行时零安装、零联网，最适合稳定运行与演示。

### 2.1 目标机加载镜像

```bash
cd "$REPO"
# 若压缩过：gunzip -c deer-flow-prod-images.tar.gz | docker load
docker load -i deer-flow-prod-images.tar

# 确认三个镜像都在
docker images | grep -E 'deer-flow-(gateway|frontend)|nginx:alpine'
```

### 2.2 启动服务

```bash
cd "$REPO"
scripts/deploy.sh start
```

> **关键**：`deploy.sh start` 执行的是 `docker compose up -d --remove-orphans` **不带 `--build`**（`scripts/deploy.sh:292-296`）。它会自动：
> - 设置 `DEER_FLOW_HOME`、`DEER_FLOW_REPO_ROOT`、`DEER_FLOW_CONFIG_PATH` 等环境变量；
> - 生成并持久化 `BETTER_AUTH_SECRET` 与 `DEER_FLOW_INTERNAL_AUTH_TOKEN`（存于 `$REPO/backend/.deer-flow/`，重启不丢）；
> - 检测到 `LocalSandboxProvider` 后仅启动 `frontend gateway nginx`，不启 provisioner、不挂 Docker socket。
>
> **切勿使用 `make up`**（默认走 `up --build`，会联网构建而失败）。

启动后访问 `http://localhost:2026`。

### 2.3 验证 LLM 与持久化

1. 宿主机确认 LLM 端点正常：
   ```bash
   curl http://localhost:11434/api/tags     # Ollama
   curl http://localhost:8000/v1/models     # vLLM
   ```
2. 浏览器访问 `http://localhost:2026`，发送一条消息，确认 Agent 能触发本地 LLM 回复。
3. 数据持久化：SQLite 位于 `$REPO/backend/.deer-flow/data/deerflow.db`（宿主机 bind mount，删容器数据保留）。

### 2.4 停止与更新

```bash
# 停止（仅停容器，数据保留）
scripts/deploy.sh down

# 更新（改代码后）
# 1. 源机修改代码 → scripts/deploy.sh build → docker save 导出
# 2. 传到目标机 → docker load -i ... → scripts/deploy.sh start
```

> 生产镜像**不挂载源码**，容器内运行的是构建时的预构建产物。任何代码变更都必须在源机重新构建镜像并重新传输。

---

## 3. 开发部署（步骤详解）

开发模式：容器挂载宿主机源码，支持后端 `uvicorn --reload` 与前端 Next.js Turbopack 热重载，可在离线环境下直接修改源码并即时生效。

### 3.1 目标机加载镜像

```bash
cd "$REPO"
docker load -i deer-flow-dev-images.tar    # 或 gunzip -c ... | docker load

docker images | grep -E 'deer-flow-dev-(gateway|frontend)|nginx:alpine'
```

### 3.2 启动服务（挂载源码）

> **关键**：开发部署**不能**使用 `scripts/docker.sh start`（它会带 `--build`，离线失败），也**不能**使用 `make docker-start`。须手动执行 `docker compose up -d` **不带 `--build`**。

> ⚠️ **【必读】重新加载 dev 镜像后，启动前必须清除旧 venv volume**
>
> `gateway-venv` named volume **只在首次创建时**从镜像 copy-up 一次 `.venv`，之后 Docker **不会**再用新镜像的 `.venv` 覆盖它。因此**只要重新加载过新 dev 镜像**（改过 `UV_EXTRAS`、`pyproject.toml`、`uv.lock`，或 rebuild 过 gateway），启动前必须先删旧 volume——否则容器仍跑旧 `.venv`，新依赖（如 `langchain-ollama`）不生效，离线时 `uv sync` 还会因联网失败而报错。
>
> ```bash
> cd "$REPO/docker"
> docker compose -p deer-flow-dev -f docker-compose-dev.yaml down
> docker volume rm deer-flow-dev_gateway-venv      # 关键：删掉旧 venv volume
> ```
>
> 全新目标机（从未跑过 dev）**可跳过**此步——volume 不存在，首次 `up` 会自动 copy-up 新镜像里的 `.venv`。

```bash
cd "$REPO"
export DEER_FLOW_ROOT="$REPO"          # dev compose 需要此变量解析 volume 路径

cd docker
docker compose -p deer-flow-dev -f docker-compose-dev.yaml up -d frontend gateway nginx
```

> 此命令会：
> - 启动 `deer-flow-dev-gateway`、`deer-flow-dev-frontend`、`nginx` 三个容器；
> - **gateway 挂载** `$REPO/backend/` → `/app/backend/`（`docker-compose-dev.yaml:137`），`dev-entrypoint.sh` 启动 `uvicorn --reload`；
> - **frontend 挂载** `$REPO/frontend/src/` → `/app/frontend/src/`、`frontend/public/` → `/app/frontend/public/`、`next.config.js` → `/app/frontend/next.config.js`（`docker-compose-dev.yaml:98-100`），运行 `pnpm run dev`（即 `next dev --turbo`）；
> - `.venv` 受 `gateway-venv` named volume 保护（`docker-compose-dev.yaml:140`），不会被宿主机空目录覆盖。但该 volume **只在首次创建时**从镜像 copy-up 一次，重新加载新镜像后须手动删除（见上方 ⚠️）；
> - `pnpm store` 挂载自宿主机（`docker-compose-dev.yaml:103`），但在离线目标机只需镜像内已有的 node_modules 即可运行。

启动后访问 `http://localhost:2026`。

### 3.3 验证 LLM

同 §2.3：先 `curl` 确认宿主机 LLM 端点，再浏览器发消息验证端到端。

### 3.4 源码修改与热重载（核心）

在**宿主机**直接修改 `$REPO` 下的源码，容器内即时生效，无需重建镜像：

| 修改范围 | 生效方式 | 是否需要重启容器 |
| --- | --- | --- |
| `backend/` 下的 `.py` 文件 | uvicorn `--reload` 自动重启 gateway | 否 |
| `config.yaml`、`.env` | 多数字段热重载；基础设施字段重启 gateway 生效 | 按需 `restart gateway` |
| `frontend/src/` 下的 `.ts/.tsx` | Next.js Turbopack 热重载 | 否 |
| `frontend/public/` 下的静态资源 | 即时替换 | 否 |
| `frontend/next.config.js` | 需重启 frontend 容器（ro mount，配置变更） | 是 |
| `backend/pyproject.toml`、`uv.lock` | 触发 `uv sync`，离线环境可能失败 | 是（须源机重建镜像） |
| `frontend/package.json`、`pnpm-lock.yaml` | 需重新 `pnpm install`，离线环境可能失败 | 是（须源机重建镜像） |

**常用操作示例**：

```bash
cd "$REPO/docker"

# 修改 backend 代码后（如 agents/memory/prompt.py），gateway 自动重载
# 日志查看
docker compose -p deer-flow-dev -f docker-compose-dev.yaml logs -f gateway

# 修改 config.yaml 后，重启 gateway 使其完全生效
docker compose -p deer-flow-dev -f docker-compose-dev.yaml restart gateway

# 修改 frontend 源码后，frontend 自动热重载
# 日志查看
docker compose -p deer-flow-dev -f docker-compose-dev.yaml logs -f frontend

# 全部日志
docker compose -p deer-flow-dev -f docker-compose-dev.yaml logs -f
```

### 3.5 局限与注意事项

- **bash 工具的文件范围**：gateway 容器仅挂载了 `backend/`、`config.yaml`、`extensions_config.json`、`skills`、`.deer-flow`。`LocalSandboxProvider` 的 `bash` 工具只能在这些挂载范围内操作。若需访问宿主机其他目录，须在 `docker-compose-dev.yaml` 的 gateway `volumes` 中新增挂载（改后需重新传输 compose 文件或直接在目标机编辑）。
- **启动时的 `uv sync`**：`dev-entrypoint.sh` 启动时会执行 `uv sync --all-packages`（`dev-entrypoint.sh:83`）。由于 `.venv` 已在镜像内完整预装，此步骤通常为秒级本地校验，不触网。若你修改了 `pyproject.toml` 或 `uv.lock`，离线环境下 `uv sync` 将失败。此时须回滚源码改动，或返回源机重建镜像。
- **dev-entrypoint.sh 的日志**：gateway 日志统一写入 `/app/logs/gateway.log`（宿主机 `$REPO/logs/gateway.log`，因 `../logs:/app/logs` 挂载）。frontend 日志写入 `/app/logs/frontend.log`（宿主机 `$REPO/logs/frontend.log`）。nginx 日志通过 Docker 标准输出查看。

### 3.6 停止与更新

```bash
cd "$REPO/docker"

# 停止开发容器（源码与数据保留在宿主机）
docker compose -p deer-flow-dev -f docker-compose-dev.yaml down

# 若需更新依赖（如新增 Python/npm 包）：
# 1. 在源机修改 pyproject.toml / package.json
# 2. 源机重新构建 dev 镜像（§1.3）
# 3. 重新导出、传输、加载
# 4. 目标机加载新镜像后，先删除旧 gateway-venv volume（见 §3.2 ⚠️），再 up -d
```

---

## 4. 调试与排错

### 4.1 日志查看（两种模式通用）

```bash
cd "$REPO/docker"

# 生产模式
docker compose -p deer-flow -f docker-compose.yaml logs -f gateway

# 开发模式
docker compose -p deer-flow-dev -f docker-compose-dev.yaml logs -f gateway
```

可在 `config.yaml` 设 `log_level: debug`，重启 gateway 后生效。

### 4.2 常见离线问题排查

| 现象 | 原因 / 解决 |
| --- | --- |
| `make up` / `make docker-start` 卡在构建 | 这些命令带 `--build`，会联网；生产改用 `scripts/deploy.sh start`，开发改用手动 `compose up -d` |
| `scripts/deploy.sh` 默认也联网 | `deploy.sh` 不带参数时走 `up --build`；**必须用 `deploy.sh start`**（不带 `--build`） |
| `scripts/docker.sh start` 同上 | `docker.sh start` 带 `--build`；开发部署**禁用** |
| gateway 启动卡住或超时 | tiktoken 联网下载编码 → 设 `memory.token_counting: char` |
| Agent 报找不到 Ollama provider | 镜像缺 `langchain-ollama`；确认 `.env` 设了 `UV_EXTRAS=ollama` 并按 §1.2 / §1.3 重新构建镜像 |
| 重新加载 dev 镜像后新依赖不生效 / `uv sync` 离线报错 | 旧 `gateway-venv` volume 未删，容器仍跑旧 `.venv`；按 §3.2 ⚠️ 删除 `deer-flow-dev_gateway-venv` 后重启 |
| LLM 连接被拒/超时 | `config.yaml` 的 `base_url` 须用 `host.docker.internal`（非 `localhost`）；确认宿主机 LLM 在监听且 `extra_hosts` 生效 |
| `host.docker.internal` 解析失败 | 确认使用项目自带的 compose 文件（已设 `extra_hosts: host.docker.internal:host-gateway`）；勿自行修改 compose 网络配置 |
| dev 模式改代码后 `uv sync` 失败 | 改了 `pyproject.toml`/`uv.lock` 导致依赖变化，但离线无法下载 → 回滚改动或源机重建镜像 |
| 前端热重载不生效 | 检查是否改的是 `package.json`/`next.config.js`（后者需重启容器）；查看 `frontend.log` |
| `docker compose up` 提示镜像不存在 | 未 `docker load` 或镜像名不符；用 `docker images` 确认对应镜像名 |
| 架构不匹配（`exec format error`） | 源机与目标机 CPU 架构不一致却未跨架构构建；按 §1.2 / §1.3 的"跨架构构建"小节，用 `DOCKER_DEFAULT_PLATFORM=linux/<目标架构>` 重建（x86→arm64 用 `linux/arm64`，反向用 `linux/amd64`） |
| `BETTER_AUTH_SECRET` 相关报错 | 生产 `deploy.sh start` 会自动生成；开发手动 compose 时若报错，须先 `export BETTER_AUTH_SECRET=$(openssl rand -hex 32)` |
| 数据丢失（删容器后） | 确认使用项目自带 compose 的 bind mount / named volume；SQLite 在 `$REPO/backend/.deer-flow`，不随容器删除 |

---

## 参考

- 服务拓扑与启动模式：`AGENTS.md`、根 `Makefile`
- 生产 compose：`docker/docker-compose.yaml`（gateway `--no-sync`、nginx `image: nginx:alpine`）
- 开发 compose：`docker/docker-compose-dev.yaml`（gateway dev-entrypoint、frontend volume mounts）
- 生产部署脚本：`scripts/deploy.sh`（`start` 不带 `--build`，自动设环境变量与 token）
- 开发入口：`docker/dev-entrypoint.sh:83`（`uv sync` + `uvicorn --reload`）
- 后端镜像：`backend/Dockerfile`（builder 缺 `--all-packages`，影响 Ollama extra）
- 前端镜像：`frontend/Dockerfile`（dev / prod 双 target）
- 配置字段：`config.example.yaml`（models/tools/database/memory/sandbox/run_events）
- MCP 默认禁用：`extensions_config.example.json`
- `token_counting` 字段与离线说明：`backend/packages/harness/deerflow/config/memory_config.py`（issues #3402 / #3429）
