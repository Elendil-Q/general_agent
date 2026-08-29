# DeerFlow 离线部署指南（Docker）

在完全气隙（无外网）的目标机上用 Docker 运行 DeerFlow 全栈**生产部署**（nginx + frontend + gateway，可选 provisioner）。

> 离线**开发**（改代码、热重载）不走本文，见 [offline_development.md](offline_development.md)。
> K3s/provisioner 沙箱模式的集群搭建见 [offline-k3s-setup.md](offline-k3s-setup.md)，本文只覆盖通用部署。

## 前提

- 目标机已安装 Docker。离线装 Docker 用官方静态二进制：`docker-27.x.tgz` 解压后 `cp docker/* /usr/local/bin/ && sudo dockerd &`
- 源机与目标机 **CPU 架构一致**；不一致时源机构建加 `DOCKER_DEFAULT_PLATFORM=linux/arm64`（按需）

## 1. 源机准备（联网）

`$REPO` 指向仓库根目录。

### 1.1 配置文件（随项目传输）

**`config.yaml`**（从 `config.example.yaml` 复制），关键点：

```yaml
models:
  - name: qwen3-local
    use: langchain_ollama:ChatOllama
    model: qwen3:32b
    base_url: http://host.docker.internal:11434   # 容器内访问宿主机 LLM，不能写 localhost
    # ...

memory:
  enabled: true
  token_counting: char      # 离线必改：默认 tiktoken 首次使用会联网下载编码

# 注释掉 web_search / web_fetch / image_search 等联网工具
```

**`.env`**（仓库根目录）：

```bash
BETTER_AUTH_SECRET=<openssl rand -base64 32>        # 必填
DEER_FLOW_INTERNAL_AUTH_TOKEN=<openssl rand -hex 32> # 必填
UV_EXTRAS=ollama          # 仅 Ollama 用户：构建时把 langchain-ollama 装入镜像
# 可选端口自定义（默认 2026/8001/3000/8002，改这里会自动同步到 compose/nginx）：
# PORT=2026
# GATEWAY_PORT=8001
# FRONTEND_PORT=3000
```

**`extensions_config.json`**：从示例复制，保持所有 `mcpServers.*.enabled: false`。

### 1.2 构建镜像

```bash
cd "$REPO"
scripts/deploy.sh build
```

构建使用仓库自带的 `backend/Dockerfile`（runtime）与 `frontend/Dockerfile`（prod target），产出三个镜像：`deer-flow-gateway`、`deer-flow-frontend`、`nginx:alpine`。

受限网络可在 `.env` 或环境变量中设置构建参数：`APT_MIRROR`、`NPM_REGISTRY`、`UV_INDEX_URL`、`UV_IMAGE`。

验证（Ollama 用户）：

```bash
docker run --rm deer-flow-gateway sh -c "cd backend && uv pip list | grep -i ollama"   # 应有输出
```

### 1.3 导出与传输

```bash
docker save deer-flow-gateway deer-flow-frontend nginx:alpine | gzip > deer-flow-images.tar.gz
```

随镜像 tar 一起传输以下项目文件（保持相对结构，`deploy.sh` 须在仓库根运行）：

```
docker/                  # compose 文件 + nginx 配置
scripts/deploy.sh        # 启动脚本
skills/                  # agent 技能（compose 挂载进容器）
config.yaml
extensions_config.json
.env
```

## 2. 目标机部署（离线）

### 2.1 加载镜像、放置文件

```bash
gunzip -c deer-flow-images.tar.gz | docker load
docker images | grep deer-flow        # 确认 gateway/frontend 都在
```

把上面列出的项目文件放到 `$REPO`。

### 2.2 启动

```bash
cd "$REPO"
scripts/deploy.sh start
```

脚本按 `config.yaml` 自动识别沙箱模式：

- **本地沙箱**（LocalSandboxProvider）：无额外步骤
- **AIO DooD 模式**：先 `docker load` AIO 沙箱镜像，脚本自动追加 `docker-compose.dood.yaml` overlay（挂载 docker socket）
- **K3s/provisioner 模式**：见 [offline-k3s-setup.md](offline-k3s-setup.md)

> **禁止任何 `--build` 行为**：`make up`、`scripts/deploy.sh`（不带参数）都会触发构建，在离线机必然失败。离线只许 `deploy.sh start` / `deploy.sh down`。

### 2.3 验证

```bash
curl http://localhost:2026/health
```

浏览器访问 `http://localhost:2026`，发一条消息确认 Agent 能调用本地 LLM 回复。运行时数据（SQLite、会话）持久化在 `$REPO/backend/.deer-flow/`，容器重建不丢。

### 2.4 更新与停止

```bash
scripts/deploy.sh down    # 停止（数据保留在 backend/.deer-flow）
```

更新流程：源机改代码 → `scripts/deploy.sh build` → `docker save` → 目标机 `docker load` → `scripts/deploy.sh start`。**改了 `pyproject.toml` / `uv.lock` / `package.json` / lockfile 必须重建镜像**（离线机无法补依赖）；纯代码改动同理（代码烘在镜像里）。详见 [update_docker.md](update_docker.md)。

## 3. 排错速查

| 现象 | 解决 |
| --- | --- |
| Agent 卡住/超时 | `config.yaml` 改 `memory.token_counting: char`（tiktoken 离线陷阱） |
| LLM 连接被拒 | 容器内访问宿主机服务用 `http://host.docker.internal:11434`，不是 `localhost` |
| `deploy.sh start` 尝试联网构建 | 误用了不带参数的 `deploy.sh` / `make up`；离线只用 `start`/`down` 子命令 |
| import langchain_ollama 失败 | 源机构建时没设 `UV_EXTRAS=ollama`，回源机重构建 |
| 页面打不开/502 | 端口冲突：改 `.env` 的 `PORT` 等，重新 `deploy.sh start`（无需重建镜像） |
| `docker load` 后容器起不来/依赖缺失 | 源/目标机架构不一致，回源机用 `DOCKER_DEFAULT_PLATFORM` 重建 |

## 参考

- 离线开发（本地 / 容器）：[offline_development.md](offline_development.md)
- K3s 沙箱集群：[offline-k3s-setup.md](offline-k3s-setup.md)
- 配置参考：`config.example.yaml`；compose 细节：`docker/docker-compose.yaml` 头注释
