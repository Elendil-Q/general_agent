# DeerFlow 离线开发指南

在完全气隙（无外网）环境下开发 DeerFlow 的两种方式：

- **本地开发**：服务直接跑在宿主机上（`make dev`），适合频繁改代码的长期开发
- **容器开发**：使用 `Dockerfile.offline-dev` 构建的一体化开发容器，宿主机只需 Docker

两种方式共用同一条原则：**所有依赖必须在联网的源机上准备好，传输到离线目标机后不再访问网络**。

---

## 1. 本地开发

服务（Gateway / Frontend / Nginx）直接运行在宿主机上，支持热重载。

### 1.1 源机准备（联网）

`$REPO` 指向仓库根目录。

**① 从零安装后端依赖，确保 uv cache 完整**

uv 增量安装会跳过已满足的包而不重新下载，导致 cache 缺包，必须从零开始：

```bash
cd "$REPO/backend"
rm -rf .venv
uv sync --all-packages --all-extras   # 含全部 extras（ollama/phoenix/postgres 等）

# 预缓存构建后端 hatchling（离线 uv sync 解析 [build-system].requires 时需要）
uv pip install --no-deps hatchling
```

**② 验证 cache 完整性（离线模拟，打包前必做）**

```bash
cd "$REPO/backend"
rm -rf .venv
uv sync --all-packages --all-extras --frozen --offline   # 成功 = cache 完整
uv run python -c "import deerflow; import fastapi; import langgraph; print('OK')"

uv cache dir                              # 确认 cache 路径（默认 ~/.cache/uv）
tar czf uv-cache.tar.gz -C ~ .cache/uv    # 打包
```

**③ 预缓存前端 pnpm store**

```bash
cd "$REPO/frontend"
rm -rf node_modules
pnpm install --frozen-lockfile
STORE_PATH=$(pnpm store path)
tar czf pnpm-store.tar.gz -C "$(dirname "$STORE_PATH")" "$(basename "$STORE_PATH")"
```

**④ 准备配置文件（随仓库传输）**

- `config.yaml`（从 `config.example.yaml` 复制）：
  - 模型指向本地推理服务，如 Ollama：`use: langchain_ollama:ChatOllama`，`base_url: http://localhost:11434`
  - **必改** `memory.token_counting: char`（默认 tiktoken 首次使用会联网下载 BPE 编码，离线会阻塞）
  - 注释掉联网工具（`web_search` / `web_fetch` / `image_search`）
- `extensions_config.json`（从示例复制）：保持所有 `mcpServers.*.enabled: false`（离线无法下载 npx 包）

**⑤ 传输清单**

```
offline-bundle/
├── repo/                # 仓库源码（排除 .venv/ node_modules/ .deer-flow/ logs/）
├── uv-cache.tar.gz
├── pnpm-store.tar.gz
├── host-tools/          # python-3.12 / node-v22 / pnpm / uv / nginx 安装包
└── llm-models/          # 可选：Ollama 模型（~/.ollama/models/ 原样打包）
```

### 1.2 目标机部署（离线）

```bash
# ① 安装宿主机工具（python3.12 / node22 / pnpm / uv / nginx），验证：
python3 --version && node -v && pnpm -v && uv --version && nginx -v

# ② 恢复包缓存
tar xzf uv-cache.tar.gz -C ~
STORE_DIR=$(dirname "$(pnpm store path)")
mkdir -p "$STORE_DIR" && tar xzf pnpm-store.tar.gz -C "$STORE_DIR"

# ③ 部署项目
cp -r offline-bundle/repo "$REPO" && cd "$REPO"

# ④ 从缓存重建依赖
cd backend  && uv sync --all-packages --all-extras --frozen --offline
cd ../frontend && pnpm install --offline --frozen-lockfile

# ⑤ 启动
cd .. && make dev        # 浏览器访问 http://localhost:2026
```

后续启动可跳过依赖同步：`./scripts/serve.sh --dev --skip-install`。

### 1.3 日常开发

代码修改即时生效（后端 uvicorn `--reload` + 前端 Turbopack HMR）。不改 `pyproject.toml` / `package.json` 就无需重新同步依赖。测试与检查全部离线可用：

```bash
cd backend && PYTHONPATH=. uv run pytest tests/ -v && make lint
cd frontend && pnpm test && pnpm check
```

### 1.4 排错速查

| 现象 | 解决 |
| --- | --- |
| `uv sync --offline` 报 "No solution found"（hatchling） | 源机漏了 §1.1① 的 hatchling 预缓存，补做后重新打包 |
| `uv sync --offline` 报 package not found | 源机/目标机 CPU 架构不一致（wheel 不匹配），两台机器架构须相同 |
| `pnpm install --offline` 报 package missing | 源机未从零安装，重做 §1.1③ |
| Agent 卡住/超时 | `config.yaml` 改 `memory.token_counting: char` |
| `make dev` 卡在依赖同步 | cache 不完整（重做 §1.1② 验证），或用 `--skip-install` 启动 |
| 离线环境 `uv sync` / `make dev` 报 DNS / Failed to fetch（如 hatchling），但缓存明明完整 | uv 不带 `--offline` 时会按 HTTP cache TTL **重校验 index 元数据**，离线 DNS 失败即硬报错。手动同步用 `uv sync --offline`；`make dev`（serve.sh 的 sync 不带 `--offline`）则先 `export UV_OFFLINE=1`。"联网跑过一次后离线就正常"只是缓存暂时新鲜，TTL 过后复现，不是修复 |

---

## 2. 容器开发

`Dockerfile.offline-dev` 把整个项目打进一个镜像：源码在 `/app/general_agent`，构建期完成依赖安装（后端 `uv sync --all-packages --all-extras`，前端 `pnpm install --frozen-lockfile`），并保留 uv cache / pnpm store 在镜像层。内置 `git`、`vim`、`nginx`、`docker` CLI；全局 Python（`/usr/local/bin/python3`）预装 numpy/pandas/matplotlib/scipy/scikit-learn/pymupdf/pdfplumber 等常用库（本地沙箱模式下 agent 的 bash 工具直接使用）；另附 provisioner 独立 venv（`/opt/provisioner-venv`，K3s 沙箱模式用）；预装 opencode（全局配置已启用 `@tarquinen/opencode-dcp` 插件，插件包已烘进 `~/.cache/opencode`，离线启动直接命中缓存）；预下载 tiktoken `cl100k_base` BPE 编码到 `/opt/tiktoken-cache`（`TIKTOKEN_CACHE_DIR` 已指向），离线也可用默认的 `token_counting: tiktoken` 精确计数，无需改成 `char`。

### 2.1 构建镜像（源机，联网）

```bash
docker build -f Dockerfile.offline-dev -t deer-flow-offline-dev .

# 受限网络可用 build args：
#   --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
#   --build-arg UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   --build-arg UV_IMAGE=<内部registry>/astral-sh/uv:latest
#   --build-arg DOCKER_CLI_IMAGE=<内部registry>/docker:cli

# 传输到离线目标机：
docker save deer-flow-offline-dev -o deer-flow-offline-dev.tar
```

### 2.2 启动开发容器（目标机，离线）

```bash
docker load -i deer-flow-offline-dev.tar

docker run -it --rm -p 2026:2026 -p 8001:8001 -p 3000:3000 deer-flow-offline-dev
```

容器内的体验与有网环境的本地开发一致——`make config` 生成配置、编辑 `config.yaml`、`make dev` 一键起全部服务（Gateway 8001 + Frontend 3000 + Nginx 2026，热重载、统一日志、Ctrl+C 全停）：

```bash
cd /app/general_agent
make config          # 从 example 模板生成 config.yaml / extensions_config.json
vim config.yaml      # 编辑模型等配置（注意下方 LLM 地址提示）
make dev             # 浏览器访问 http://localhost:2026
```

> **LLM 地址**：模型服务（如 Ollama）跑在**宿主机**上时，容器内 `localhost` 指容器自己。
> `config.yaml` 里的 `base_url` 要写 `http://host.docker.internal:11434`，并在
> `docker run` 加 `--add-host host.docker.internal:host-gateway`（macOS/Windows
> Docker Desktop 内置该域名，无需 `--add-host`）。

> **内置离线环境变量**：镜像烘了 `UV_OFFLINE=1`（uv 默认只用缓存、永不联网）和
> `UV_INDEX_URL=<构建期同值>`（uv 元数据缓存按 index URL 分桶，**运行时勿覆盖**）。
> 故意联网装新包时用 `UV_OFFLINE=0 uv add <pkg>` 或 `docker run -e UV_OFFLINE=0`。
>
> **离线安全**：`make dev` 每次启动会跑 `uv sync` / `pnpm install`。lockfile 未变更且
> 缓存完整时不触网（秒级校验）；也可 `./scripts/serve.sh --dev --skip-install` 跳过。

### 2.3 挂载本地源码开发

```bash
docker run -it --rm -p 2026:2026 -p 8001:8001 -p 3000:3000 \
  -v $(pwd):/app/general_agent \
  deer-flow-offline-dev
```

挂载会遮盖镜像内预装的 `.venv` / `node_modules`，用镜像内保留的缓存离线重装：

```bash
cd /app/general_agent/backend  && uv sync --all-packages --all-extras --frozen --offline
cd /app/general_agent/frontend && pnpm install --frozen-lockfile --offline
cd /app/general_agent && make dev
```

> **必须带 `--all-extras`**，否则 `uv sync` 会把镜像预装的 extras（ollama/phoenix 等）裁剪掉。
> 挂载模式下宿主机仓库里已有的 `config.yaml` / `extensions_config.json` 直接生效，
> 无需 `make config`（注意镜像构建时这两个文件已被 dockerignore 排除，不会烘进镜像）。

### 2.4 AIO 沙箱调试：DooD 模式（本地 Docker）

Gateway 经容器内 docker CLI 操作宿主机 Docker daemon 起沙箱容器。

**前提**：宿主机已 `docker load` AIO 沙箱镜像；`config.yaml` 配置：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  image: <本地 AIO 镜像 tag>
```

**启动**（沙箱 volume mount 的 source 是宿主机路径，所以必须挂载源码；`--add-host` 仅 Linux 需要）：

```bash
docker run -it --rm -p 2026:2026 -p 8001:8001 -p 3000:3000 \
  -v $(pwd):/app/general_agent \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --add-host host.docker.internal:host-gateway \
  -e DEER_FLOW_SANDBOX_HOST=host.docker.internal \
  -e DEER_FLOW_HOST_BASE_DIR=$(pwd)/backend/.deer-flow \
  -e DEER_FLOW_HOST_SKILLS_PATH=$(pwd)/skills \
  deer-flow-offline-dev
```

验证：跑一次带 bash 工具调用的会话，宿主机 `docker ps` 应出现 `deer-flow-sandbox-*` 容器。

### 2.5 AIO 沙箱调试：K3s + provisioner 模式

每个沙箱一个 Pod，由 provisioner 经 K8s API 管理（Pod `imagePullPolicy: IfNotPresent`，适合离线）。

**前提（宿主机，有网时准备）**：

```bash
# ① k3s air-gap 安装（k3s binary + k3s-airgap-images tar，见 tutorials/offline-k3s-setup.md）
# ② AIO 镜像导入 k3s 的 containerd —— 注意 docker load 对 k3s 无效：
sudo k3s ctr images import all-in-one-sandbox.tar
```

**启动**（`--add-host` 仅 Linux 需要）：

```bash
docker run -it --rm -p 2026:2026 -p 8001:8001 -p 3000:3000 \
  -v $(pwd):/app/general_agent \
  -v /etc/rancher/k3s/k3s.yaml:/root/.kube/config:ro \
  --add-host host.docker.internal:host-gateway \
  deer-flow-offline-dev
```

容器内先起 provisioner（镜像已内置其运行环境）：

```bash
cd /app/general_agent/docker/provisioner && \
  K8S_API_SERVER=https://host.docker.internal:6443 \
  NODE_HOST=host.docker.internal \
  SANDBOX_IMAGE=<本地 AIO 镜像 tag> \
  SKILLS_HOST_PATH=/app/general_agent/skills \
  THREADS_HOST_PATH=/app/general_agent/backend/.deer-flow/threads \
  /opt/provisioner-venv/bin/uvicorn app:app --host 0.0.0.0 --port 8002 &
```

`config.yaml` 配置：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://localhost:8002
```

验证：跑一次带 bash 工具调用的会话，宿主机 `sudo k3s kubectl get pods -n deer-flow` 应出现沙箱 Pod。

---

## 参考

- K3s 离线安装：[offline-k3s-setup.md](offline-k3s-setup.md)
- Docker 全栈离线部署：[offline-docker-setup.md](offline-docker-setup.md)
- 配置参考：`config.example.yaml`；服务拓扑：根目录 `AGENTS.md`
