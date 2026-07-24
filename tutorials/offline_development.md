# DeerFlow 离线本地开发指南

本指南说明如何在**完全气隙（无外网）**的环境下，以**本地原生开发**方式（`make dev`，非 Docker）搭建并运行 DeerFlow 开发环境。所有服务（Gateway、Frontend、Nginx）直接运行在宿主机上，支持热重载。

> 如果你更倾向于在离线环境使用 Docker 全栈（容器内运行一切，宿主机零安装），请参阅 [offline-docker-setup.md](offline-docker-setup.md)。

---

## 0. 与 Docker 离线方案的对比

| 维度 | 本地原生开发（本指南） | Docker 全栈（offline-docker-setup.md） |
| --- | --- | --- |
| 宿主机安装 | 需安装 Node.js、pnpm、uv、nginx、Python | 仅需 Docker |
| 启动方式 | `make dev`（直接运行进程） | `docker compose up -d` |
| 热重载 | 后端 uvicorn --reload + 前端 Turbopack HMR | 同左（full-dev 模式） |
| 依赖缓存 | uv cache + pnpm store（宿主机本地） | 全部在镜像内 |
| 依赖变更 | 可离线添加（需预缓存或离线安装） | 须源机重建镜像再传输 |
| LLM 连接 | `localhost` 直接访问 | `host.docker.internal` 经 Docker 网关 |
| 适用场景 | 频繁改代码、调试、跑测试的长期开发 | 部署演示、环境隔离 |

---

## 1. 宿主机工具清单

目标机（离线）必须预装以下工具。所有安装包需在源机（联网）下载后传输。

### 1.1 必需工具

| 工具 | 最低版本 | 用途 | 下载地址 |
| --- | --- | --- | --- |
| **Python** | 3.12+ | 后端运行时 | https://www.python.org/downloads/ |
| **Node.js** | 22+ | 前端运行时 | https://nodejs.org/en/download/ |
| **pnpm** | 10.26.2+ | 前端包管理 | https://github.com/pnpm/pnpm/releases |
| **uv** | 最新 | 后端包管理 + 虚拟环境 | https://github.com/astral-sh/uv/releases |
| **nginx** | 任意稳定版 | 反向代理 | https://nginx.org/en/download.html |
| **Git** | 任意 | 版本控制（可选，便于 diff） | https://git-scm.com/downloads |

### 1.2 可选工具

| 工具 | 用途 |
| --- | --- |
| **Ollama** | 本地 LLM 推理（推荐，部署简单） |
| **vLLM** | 本地 LLM 推理（高性能，需 GPU） |
| **openssl** | 生成 `BETTER_AUTH_SECRET`（通常系统自带） |
| **lsof** / **ss** | `serve.sh` 端口检测与进程管理 |

---

## 2. 源机准备（联网环境）

以下操作在**联网的源机器**上执行。`$REPO` 指向仓库根目录。

### 2.1 克隆仓库

```bash
git clone <repo-url> "$REPO"
cd "$REPO"
```

### 2.2 从零安装后端依赖（确保 uv cache 完整）

**这一步是离线成功的关键。** uv cache 必须包含 `uv.lock` 中锁定的**每一个包**（含全部传递依赖），否则目标机 `uv sync --offline` 会因缺包而失败。

uv 在增量安装时会跳过已满足的包而不重新下载，导致 cache 缺失。因此必须从零开始。

#### 2.2.1 清除旧环境，从零安装

```bash
cd "$REPO/backend"

# 删除已有虚拟环境，强制 uv 重新解析并下载所有包到 cache
rm -rf .venv

# 从零同步（--all-packages 确保 workspace 成员 deerflow-harness 也安装）
uv sync --all-packages

# 如需 Ollama 支持，加装 extra（会额外下载 langchain-ollama 到 cache）
uv sync --all-packages --extra ollama
```

> **为什么必须删 `.venv`？** 如果 `.venv` 已存在，uv 会跳过已满足的包而不重新下载，导致 cache 中缺失这些包。目标机是全新环境，需要从 cache 重新安装全部依赖，缺任何一个都会失败。

#### 2.2.2 预缓存构建后端依赖

`uv sync` 只缓存运行时依赖，**不缓存构建后端依赖**（如 `hatchling`）。但 `uv sync --offline` 在目标机解析 workspace 成员的 `[build-system].requires` 时需要 hatchling。项目 Dockerfile 中专门做了 `uv pip download --no-deps hatchling` 处理：

```bash
cd "$REPO/backend"

# 预下载 hatchling（deerflow-harness 的构建后端）到 uv cache
uv pip download --no-deps hatchling
```

> Dockerfile 注释：`uv sync --frozen --offline` 会因找不到 hatchling 而报 "No solution found when resolving: hatchling"。

#### 2.2.3 验证 cache 完整性（离线模拟）

打包前必须验证——删除 `.venv` 后用 `--frozen --offline` 重建，能成功则说明 cache 完整：

```bash
cd "$REPO/backend"
rm -rf .venv

# 模拟离线环境：--frozen 严格使用 uv.lock，--offline 禁止联网
uv sync --all-packages --extra ollama --frozen --offline

# 成功 = cache 完整，可安全传输
# 失败 = cache 缺包，回到 2.2.1 重新清理安装

# 验证关键依赖可导入
uv run python -c "import deerflow; import fastapi; import langgraph; print('core OK')"
uv run python -c "import langchain_ollama; print('ollama OK')"  # 仅 Ollama 用户
```

> `--frozen` 让 uv 严格使用 `uv.lock` 中的锁定版本。`--offline` 禁止任何网络请求。两者组合是离线环境的正确用法。

#### 2.2.4 打包 uv cache

```bash
# 确认 cache 路径（默认 ~/.cache/uv）
uv cache dir

# 打包整个 cache 目录
tar czf uv-cache.tar.gz -C ~ .cache/uv
```

> 如果 `~/.cache/uv` 不可写（如沙箱环境），Makefile 会自动回退到 `/tmp/uv-cache`。用 `uv cache dir` 确认实际路径后打包。

### 2.3 预缓存前端 Node 包（pnpm store）

pnpm 使用全局 store + 硬链接机制。从零安装后 store 中包含所有依赖。

```bash
cd "$REPO/frontend"

# 从零安装（如果已有 node_modules，先删除确保 store 完整）
rm -rf node_modules
pnpm install --frozen-lockfile

# 打包 store
STORE_PATH=$(pnpm store path)
tar czf pnpm-store.tar.gz -C "$(dirname "$STORE_PATH")" "$(basename "$STORE_PATH")"
```

### 2.4 预缓存 tiktoken 编码

DeerFlow 默认使用 `tiktoken` 做 token 计数（`memory.token_counting: tiktoken`）。tiktoken 首次使用时会从 OpenAI CDN 下载 BPE 编码文件，离线环境会超时阻塞（issues #3402 / #3429）。

**方案 A：预缓存编码**

```bash
cd "$REPO/backend"
uv run python -c "
import tiktoken
enc = tiktoken.get_encoding('cl100k_base')
enc = tiktoken.get_encoding('o200k_base')
print('tiktoken encodings cached')
"

# 查找并打包缓存文件
find / -path '*tiktoken*' -name '*.tiktoken' 2>/dev/null | head -5
```

**方案 B（推荐）：改用字符计数**

在 `config.yaml` 中设置 `memory.token_counting: char`，完全绕过 tiktoken 的网络下载：

```yaml
memory:
  enabled: true
  token_counting: char
```

> 离线开发推荐方案 B，除非你对 token 预算精度有严格要求。

### 2.5 准备配置文件

在源机上编辑好以下配置文件，随仓库传输。

#### config.yaml

从 `config.example.yaml` 复制，关键修改点：

```yaml
# ── 模型配置以 Ollama 为例 ──────────────────────────────────
models:
  - name: qwen3-local
    display_name: Qwen3 (Ollama)
    use: langchain_ollama:ChatOllama
    model: qwen3:32b
    base_url: http://localhost:11434    # 本地 localhost，非 host.docker.internal
    num_predict: 8192
    temperature: 0.7
    reasoning: true
    supports_thinking: true
    supports_vision: false

# ── 存储 ────────────────────────────────────────────────────
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data

# ── 记忆（离线关键：避免 tiktoken 联网） ───────────────────
memory:
  enabled: true
  token_counting: char              # 离线必改

# ── 工具：注释联网工具 ─────────────────────────────────────
tools:
  - name: bash
    group: bash
    use: deerflow.sandbox.tools:bash_tool
  - name: ls
    group: file:read
    use: deerflow.sandbox.tools:ls_tool
  - name: read_file
    group: file:read
    use: deerflow.sandbox.tools:read_file_tool
  - name: write_file
    group: file:write
    use: deerflow.sandbox.tools:write_file_tool
  - name: str_replace
    group: file:write
    use: deerflow.sandbox.tools:str_replace_tool
  - name: glob
    group: file:read
    use: deerflow.sandbox.tools:glob_tool
  - name: grep
    group: file:read
    use: deerflow.sandbox.tools:grep_tool
  # 以下联网工具在离线环境无法使用，注释掉
  # - name: web_search
  # - name: web_fetch
  # - name: image_search

# ── 沙箱（本地文件系统） ──────────────────────────────────
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: true

# ── 运行事件 ────────────────────────────────────────────────
run_events:
  backend: memory
```

> vLLM 用户：使用内置 `deerflow.models.vllm_provider:VllmChatModel` provider，无需 extra。`base_url: http://localhost:8000/v1`。

#### extensions_config.json

从 `extensions_config.example.json` 复制。**保持所有 `mcpServers.*.enabled: false`**。MCP 服务器（如 `npx -y @modelcontextprotocol/server-github`）在离线环境无法下载 npx 包。

#### .env（新建）

```bash
# 通常留空即可
# 如需修改端口：PORT=2026 / GATEWAY_PORT=8001 / FRONTEND_PORT=3000
```

#### frontend/.env

空文件或从 `.env.example` 复制默认值。`make dev` 通过 nginx 代理，无需手动设置后端 URL。

### 2.6 传输清单

```
offline-bundle/
├── repo/                    # 仓库源码（含已编辑 config.yaml / extensions_config.json / .env）
│                            # 排除 backend/.venv/ frontend/node_modules/ frontend/.next/
│                            # 排除 backend/.deer-flow/ logs/ __pycache__/
├── uv-cache.tar.gz          # uv 全局缓存（含所有运行时依赖 + 构建后端依赖 hatchling）
├── pnpm-store.tar.gz        # pnpm 全局 store
├── host-tools/              # 宿主机工具安装包
│   ├── python-3.12.x.tar.gz
│   ├── node-v22.x.x.tar.xz
│   ├── pnpm
│   ├── uv
│   └── nginx.tar.gz
└── llm-models/              # 可选：Ollama 模型权重
```

> **关于 Ollama**：在源机 `ollama pull qwen3:32b`，模型在 `~/.ollama/models/`。打包传输后解压到目标机相同路径即可。

---

## 3. 目标机配置（离线环境）

以下操作在**离线的目标机器**上执行。

### 3.1 安装宿主机工具

以 Linux 为例：

```bash
# Python
tar xzf host-tools/python-3.12.x.tar.gz -C /usr/local
ln -sf /usr/local/python3.12/bin/python3.12 /usr/local/bin/python3

# Node.js
tar xJf host-tools/node-v22.x.x.tar.xz -C /usr/local
ln -sf /usr/local/node-v22.x.x-*/bin/node /usr/local/bin/node
ln -sf /usr/local/node-v22.x.x-*/bin/npm /usr/local/bin/npm

# pnpm（单文件二进制）
cp host-tools/pnpm /usr/local/bin/pnpm
chmod +x /usr/local/bin/pnpm

# uv（单文件二进制）
cp host-tools/uv /usr/local/bin/uv
chmod +x /usr/local/bin/uv

# nginx
tar xzf host-tools/nginx.tar.gz -C /usr/local
ln -sf /usr/local/nginx/sbin/nginx /usr/local/bin/nginx
```

验证：

```bash
python3 --version && node -v && pnpm -v && uv --version && nginx -v
```

### 3.2 恢复包缓存

```bash
# uv cache
tar xzf uv-cache.tar.gz -C ~

# pnpm store
STORE_DIR=$(dirname "$(pnpm store path)")
mkdir -p "$STORE_DIR"
tar xzf pnpm-store.tar.gz -C "$STORE_DIR"
```

### 3.3 部署项目

```bash
cp -r offline-bundle/repo "$REPO"
cd "$REPO"
```

### 3.4 恢复本地 LLM

**Ollama：**

```bash
# 恢复模型文件
mkdir -p ~/.ollama/models
cp -r llm-models/* ~/.ollama/models/

# 启动
ollama serve &
ollama list                    # 应显示已安装的模型
```

**vLLM：** 启动推理服务后验证 `curl http://localhost:8000/v1/models`。

### 3.5 从缓存重建后端虚拟环境

```bash
cd "$REPO/backend"

# --frozen 使用锁定的 uv.lock，--offline 禁用网络，--all-packages 含 workspace 成员
uv sync --all-packages --extra ollama --frozen --offline

# 验证
uv run python -c "import deerflow; import fastapi; import langgraph; print('OK')"
```

> 如果报 "No solution found when resolving: hatchling"，说明源机未执行 §2.2.2 预缓存 hatchling。回源机执行后重新打包传输。

### 3.6 从缓存重建前端 node_modules

```bash
cd "$REPO/frontend"
pnpm install --offline --frozen-lockfile

# 验证
pnpm ls --depth 0 | head -20
```

---

## 4. 启动开发服务

### 4.1 启动

```bash
cd "$REPO"
make dev
```

`make dev` 执行流程：

1. `scripts/check.py` — 检查工具版本
2. `scripts/serve.sh --dev` — 启动三个服务：
   - **Gateway**（8001）：`uvicorn --reload` 热重载
   - **Frontend**（3000）：`next dev --turbo` HMR
   - **Nginx**（2026）：反向代理统一入口

> **关键**：`serve.sh` 每次启动默认会运行 `uv sync` 和 `pnpm install`。在离线环境下，只要 cache 完整，这些操作只做校验（秒级），不会联网失败。但如果 `uv.lock` 或 `package.json` 有变更，会尝试联网并失败。

### 4.2 跳过依赖安装启动（推荐）

首次 `uv sync --offline` 和 `pnpm install --offline` 成功后，后续启动可跳过依赖同步：

```bash
./scripts/serve.sh --dev --skip-install
```

### 4.3 验证

启动成功后浏览器访问 `http://localhost:2026`：

1. 应看到 DeerFlow 前端界面
2. 发送消息，确认 Agent 能调用本地 LLM 回复
3. 修改后端 `.py` 文件，确认 uvicorn 自动重载
4. 修改前端文件，确认 Turbopack HMR 生效

### 4.4 停止

```bash
make stop
# 或 Ctrl+C（前台模式）
```

---

## 5. 离线开发工作流

### 5.1 日常开发

代码修改即时生效（后端 uvicorn `--reload` + 前端 Turbopack HMR）。**只要不改动 `pyproject.toml` / `package.json`，无需重新同步依赖。**

### 5.2 运行测试

```bash
# 后端测试
cd "$REPO/backend"
PYTHONPATH=. uv run pytest tests/ -v

# 前端测试
cd "$REPO/frontend"
pnpm test
```

测试不依赖网络，完全本地运行。

### 5.3 代码检查

```bash
# 后端
cd "$REPO/backend"
make lint
make format

# 前端
cd "$REPO/frontend"
pnpm check
```

`ruff` 和 `eslint` 的规则已随依赖一起缓存，离线环境可直接使用。

### 5.4 添加新依赖（挑战）

在离线环境中添加新依赖是最复杂的操作。有几种方案：

**方案 1：回源机添加后重新打包（最可靠）**

在源机上改 `pyproject.toml`，重新 `uv sync` + 打包 cache，传输到目标机。

**方案 2：从预先下载的 .whl 离线安装**

```bash
# 在源机下载包及其所有传递依赖
cd "$REPO/backend"
uv pip download new-package -d /tmp/offline-pkgs

# 打包 /tmp/offline-pkgs 传输到目标机
# 在目标机离线安装
uv pip install --no-index --find-links /tmp/offline-pkgs new-package
```

**方案 3：使用 uv 的 `--offline` 提前全局缓存**

在源机上为任何可能需要的包预先下载到 cache：

```bash
# 在源机预下载常用包
uv pip download --no-deps numpy pandas matplotlib
```

这样只要目标机 cache 中有包，就可以 `uv pip install --offline` 安装。

> 建议在项目计划阶段就确定依赖，由源机一次性 cache，避免在目标机频繁处理依赖变更。

---

## 6. 已知陷阱与排错

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `uv sync --offline` 报 "No solution found" | cache 不完整（未从零安装或缺失 hatchling） | 源机重新 `rm -rf .venv && uv sync --all-packages` + `uv pip download --no-deps hatchling`，重新打包传输 |
| `uv sync --offline` 报 package not found | 源机和目标机 CPU 架构不同（wheel 不匹配）| 确保源机和目标机 `uname -m` 一致。不一致需在源机跨架构构建 |
| `pnpm install --offline` 报 package missing | pnpm store 不完整 | 源机 `rm -rf node_modules && pnpm install --frozen-lockfile`，重新打包 store |
| Agent 卡住或超时 | tiktoken 尝试联网下载编码 | 改 `config.yaml` 中 `memory.token_counting: char` |
| LLM 连接被拒 | base_url 用了 `host.docker.internal` | 本地开发改用 `http://localhost:11434` |
| `make dev` 卡在依赖同步 | `serve.sh` 执行 `uv sync` 时尝试联网 | 确保 cache 完整（§2.2.3 验证），或 `./scripts/serve.sh --dev --skip-install` |
| import langchain_ollama 失败 | 源机未安装 Ollama extra | 源机执行 `uv sync --all-packages --extra ollama` 后重新打包 |
| `make check` 报 pnpm 版本不对 | pnpm 版本 < 10.26.2 | 从源机下载正确版本的 pnpm 二进制 |
| 改代码后热重载不生效 | 改了配置文件（`config.yaml`）| 配置文件修改需重启 Gateway：`make stop && make dev` |
| nginx 启动失败 | 端口被占用 | 检查 `PORT`/`GATEWAY_PORT`/`FRONTEND_PORT` 是否冲突 |

### 特殊场景：跨架构

如果源机和目标机 CPU 架构不同（如源机 x86_64，目标机 ARM64）：

- Python 包必须下载 **两种架构的 wheel**（或确保有 source distribution）
- Node 原生模块（`@esbuild/*`、`@next/swc-*` 等）必须匹配目标架构
- **强烈建议源机和目标机使用相同 CPU 架构**，否则离线开发复杂度显著增加
- 跨架构场景更推荐使用 [offline-docker-setup.md](offline-docker-setup.md) 的 Docker 方案（跨架构构建镜像）

---

## 7. 关键配置参考

### 环境变量速查

| 变量 | 用途 | 默认 |
| --- | --- | --- |
| `PORT` | Nginx 监听端口 | `2026` |
| `GATEWAY_PORT` | Gateway API 端口 | `8001` |
| `FRONTEND_PORT` | Frontend 端口 | `3000` |
| `UV_CACHE_DIR` | uv cache 目录（可写性检测自动回退） | `~/.cache/uv` |
| `DEER_FLOW_PROJECT_ROOT` | 项目根目录 | 脚本自动检测 |

### 端口映射

```
浏览器 → localhost:2026 (Nginx)
                ├── /api/langgraph/* → localhost:8001 (Gateway agent runtime)
                ├── /api/*           → localhost:8001 (Gateway REST API)
                └── /                → localhost:3000 (Frontend)
```

### 核心依赖关系

```
backend/
├── deerflow-harness (workspace 成员)
│   └── 构建后端: hatchling (需预缓存)
├── fastapi, httpx, uvicorn (Web 框架)
├── langgraph, langchain, langchain-openai (Agent 核心)
├── langchain-ollama (Ollama 本地 LLM，optional extra)
├── tiktoken (token 计数，离线推荐改用 char)
├── sqlalchemy, aiosqlite (SQLite 存储)
├── markitdown (文档转换)
└── 开发依赖: pytest, ruff, blockbuster

frontend/
├── next, react, react-dom (UI 框架)
├── @langchain/langgraph-sdk (Agent 通信)
├── tailwindcss, @radix-ui/* (UI 组件)
├── codemirror, @uiw/react-codemirror (代码编辑器)
└── 开发依赖: typescript, eslint, playwright
```

---

## 参考

- Docker 离线部署：[offline-docker-setup.md](offline-docker-setup.md)
- 服务拓扑：根目录 `AGENTS.md`、`Makefile`
- 后端开发：`backend/AGENTS.md`、`backend/Makefile`
- 前端开发：`frontend/AGENTS.md`
- 配置参考：`config.example.yaml`、`.env.example`
- 启动脚本：`scripts/serve.sh`、`scripts/check.py`
- Dockerfile（离线参考）：`backend/Dockerfile`（hatchling 离线处理详见 §2.2.2）

## 8. 中间件容器化（混合模式）

在目标机上安装 nginx 可能不方便（依赖缺失、权限问题、系统包版本过旧等），而 K8s provisioner 本就是容器化服务（需要 Python + kubectl + K8s 网络环境）。这一节介绍**混合开发模式**：Gateway 和 Frontend 仍运行在宿主机上享受热重载，nginx 和 provisioner 则用 Docker 运行，减少宿主机安装负担。

### 8.1 适用场景

| 场景 | 推荐方式 |
| --- | --- |
| 宿主机不便于安装 nginx（如 macOS 打包限制、无包管理器） | nginx 容器化 |
| 宿主机已安装 nginx，但配置复杂 | nginx 容器化 |
| 使用 K8s/provisioner 沙箱模式 | provisioner 容器化 |
| 使用 Docker(DooD)沙箱模式 | 挂载 Docker socket 即可，无需额外服务 |
| 宿主机已有 nginx 且配置简单 | 直接用本地 nginx（§1） |

### 8.2 架构

```
浏览器 → localhost:2026 (nginx 容器)
                ├── /api/langgraph/* → host.docker.internal:8001 (宿主机 Gateway)
                ├── /api/*           → host.docker.internal:8001 (宿主机 Gateway)
                └── /                → host.docker.internal:3000 (宿主机 Frontend)

Provisner 容器 → K8s API Server（管理沙箱 Pod）
Gateway 宿主机 → host.docker.internal:8002 (Provisner 容器)  [沙箱管理请求]
Gateay 宿主机 → /var/run/docker.sock                          [DooD 沙箱模式]
```

关键要点：Gateway/Frontend 在宿主机上以 `localhost:8001` / `localhost:3000` 运行，nginx 容器通过 `host.docker.internal` 访问宿主机服务。

### 8.3 nginx 容器化

#### 8.3.1 创建混合模式 nginx 配置

从 Docker nginx 配置修改 upstream，指向宿主机：

```bash
# 从 docker/nginx/nginx.conf 复制
cp docker/nginx/nginx.conf docker/nginx/nginx.hybrid.conf
```

修改 `nginx.hybrid.conf` 中的 upstream 定义：

```nginx
# 原 Docker config：解析容器内服务名
# set $gateway_upstream gateway:__GATEWAY_PORT__;
# set $frontend_upstream frontend:__FRONTEND_PORT__;

# 混合模式：指向宿主机 localhost（经 host.docker.internal）
set $gateway_upstream host.docker.internal:8001;
set $frontend_upstream host.docker.internal:3000;
```

> 直接在 `nginx.hybrid.conf` 中写入具体端口（8001/3000），无需模板变量替换，避免在容器启动时依赖 sed 变量替换逻辑。

#### 8.3.2 创建中间件 docker-compose

创建 `docker/docker-compose.hybrid.yaml`：

```yaml
# DeerFlow 混合开发模式
# Gateway + Frontend 在宿主机热重载，nginx + provisioner 在 Docker 中运行
#
# 用法：docker compose -f docker/docker-compose.hybrid.yaml up -d
# 停止：docker compose -f docker/docker-compose.hybrid.yaml down

services:
  nginx:
    image: nginx:alpine
    container_name: deer-flow-nginx-hybrid
    ports:
      - "2026:2026"
    volumes:
      - ./nginx/nginx.hybrid.conf:/etc/nginx/nginx.conf:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - hybrid
    restart: unless-stopped

networks:
  hybrid:
    driver: bridge
```

> **`extra_hosts: host.docker.internal:host-gateway`** 是关键：让 nginx 容器内的 `host.docker.internal` 解析到 Docker 宿主机 IP。
> - Linux：自动解析到 Docker 网桥网关（`172.17.0.1`）
> - macOS/Windows（Docker Desktop）：内置支持，无需额外配置

#### 8.3.3 启动

```bash
cd "$REPO"

# 1. 先在宿主机上启动 Gateway 和 Frontend
cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app \
    --host 0.0.0.0 --port 8001 --reload --log-level debug &
cd ..

# 2. 启动前端
cd frontend && pnpm dev &
cd ..

# 3. 启动 nginx 容器
docker compose -f docker/docker-compose.hybrid.yaml up -d

# 4. 验证
curl http://localhost:2026/health
# 浏览器访问 http://localhost:2026
```

#### 8.3.4 离线准备

nginx 容器镜像（`nginx:alpine`）需要在源机预拉取并导出：

```bash
# 源机
docker pull nginx:alpine
docker save nginx:alpine -o nginx-alpine.tar

# 传输到目标机
# 目标机
docker load -i nginx-alpine.tar
```

> 如果目标机已完全离线且未安装 Docker，需要先离线安装 Docker（见 §8.6）。

### 8.4 K8s Provisioner 容器化

K8s provisioner 本身是为容器化设计的——它需要 `kubectl`、K8s API 访问、以及 Python 环境，直接运行在宿主机上反而复杂。

#### 8.4.1 扩展混合 docker-compose

将 provisioner 服务加入 `docker/docker-compose.hybrid.yaml`：

```yaml
services:
  nginx:
    # ... 同上 ...

  provisioner:
    build:
      context: ./provisioner
      dockerfile: Dockerfile
      args:
        APT_MIRROR: ${APT_MIRROR:-}
        PIP_INDEX_URL: ${PIP_INDEX_URL:-}
    image: deer-flow-provisioner:hybrid
    container_name: deer-flow-provisioner-hybrid
    volumes:
      - ~/.kube/config:/root/.kube/config:ro
    environment:
      - K8S_NAMESPACE=deer-flow
      - SANDBOX_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
      - SKILLS_HOST_PATH=${DEER_FLOW_REPO_ROOT}/skills
      - THREADS_HOST_PATH=${DEER_FLOW_REPO_ROOT}/backend/.deer-flow/threads
      - KUBECONFIG_PATH=/root/.kube/config
      - NODE_HOST=host.docker.internal
      - K8S_API_SERVER=https://host.docker.internal:26443
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - hybrid
    restart: unless-stopped
```

> `K8S_API_SERVER` 和 `NODE_HOST` 指向 `host.docker.internal`，使 provisioner 能访问宿主机上的 K8s API 和 Node 服务。

#### 8.4.2 宿主机的 Gateway 配置

Gateway 在宿主机上运行，通过 `localhost:8002` 访问 provisioner。在 `config.yaml` 中：

```yaml
sandbox:
  use: deerflow.sandbox.provisioner:ProvisionerSandboxProvider
  provisioner_url: http://localhost:8002
```

> 如果 provisioner 映射到宿主机端口 8002，Gateway 用 `localhost:8002`；如果 provisioner 仅在内网侦听（不映射端口），Gateway 用 `host.docker.internal:8002`（仅当 Gateway 也在 Docker 中时有效）。混合模式下 Gateway 在宿主机，provisioner 在容器，建议映射端口。

### 8.5 DooD 沙箱模式（无需额外容器）

DooD（Docker-out-of-Docker）模式下，Gateway 直接通过 Docker socket 启动沙箱容器，不需要 nginx 或 provisioner。唯一的中间件是 Docker 自身。

```yaml
# config.yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  # 不配置 provisioner_url，使用本地 Docker socket
```

宿主机只需确保 Docker daemon 运行且当前用户有权限访问 `/var/run/docker.sock`。

### 8.6 离线安装 Docker

如果目标机完全没有 Docker，需要从源机传输 Docker 安装包。

**Linux（推荐使用官方二进制）：**

```bash
# 源机：下载 Docker 静态二进制
wget https://download.docker.com/linux/static/stable/x86_64/docker-27.0.0.tgz
# 传输 docker-27.0.0.tgz + nginx-alpine.tar 到目标机

# 目标机：安装
tar xzf docker-27.0.0.tgz
sudo cp docker/* /usr/local/bin/
sudo dockerd &              # 启动 daemon
docker load -i nginx-alpine.tar
```

> 注意：Docker daemon（`dockerd`）需要 root 权限和一些内核模块。对于纯开发用途，`dockerd --iptables=false --bridge=none` 可减少权限需求。

**macOS/Windows：** 离线安装比较困难（依赖系统级安装包）。建议在源机上确认目标平台有预编译的 Docker Desktop 离线安装包。

### 8.7 完整混合 docker-compose 参考

```yaml
# docker/docker-compose.hybrid.yaml
# 用法：docker compose -f docker/docker-compose.hybrid.yaml up -d [服务名]

services:
  nginx:
    image: nginx:alpine
    container_name: deer-flow-nginx-hybrid
    ports:
      - "${PORT:-2026}:2026"
    volumes:
      - ./nginx/nginx.hybrid.conf:/etc/nginx/nginx.conf:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - hybrid
    restart: unless-stopped

  provisioner:
    # 仅 K8s/Provisioner 沙箱模式需要
    profiles:
      - sandbox
    build:
      context: ./provisioner
      dockerfile: Dockerfile
    image: deer-flow-provisioner:hybrid
    container_name: deer-flow-provisioner-hybrid
    ports:
      - "8002:8002"
    volumes:
      - ~/.kube/config:/root/.kube/config:ro
    environment:
      - K8S_NAMESPACE=deer-flow
      - SANDBOX_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
      - SKILLS_HOST_PATH=${DEER_FLOW_REPO_ROOT:-.}/skills
      - THREADS_HOST_PATH=${DEER_FLOW_REPO_ROOT:-.}/backend/.deer-flow/threads
      - KUBECONFIG_PATH=/root/.kube/config
      - NODE_HOST=host.docker.internal
      - K8S_API_SERVER=https://host.docker.internal:26443
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - hybrid
    restart: unless-stopped

networks:
  hybrid:
    driver: bridge
```

> Provisioner 使用 `profiles: [sandbox]`，默认不启动。需要时执行：
> ```bash
> docker compose -f docker/docker-compose.hybrid.yaml --profile sandbox up -d provisioner
> ```

### 8.8 混合模式的优缺点

| 优点 | 缺点 |
| --- | --- |
| 宿主机无需安装 nginx，减少环境依赖 | 增加 Docker daemon 运行开销 |
| nginx 配置隔离在容器中，不污染宿主机 | `host.docker.internal` 在 Linux 需 Docker 20.10+ + `host-gateway` 特性 |
| provisioner 环境标准化，避免 Python/kubectl 版本冲突 | 多一层网络跳转，增加微小的延迟 |
| 与生产部署的 compose 文件结构一致，便于切换 | 离线部署 Docker 本身需要额外准备（见 §8.6） |
