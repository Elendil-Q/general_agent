# 前端更新操作手册（基于已有 dev 镜像）

> 适用场景：你已经在离线 arm64 刀片上通过 `make docker-start` 跑过 dev 栈，
> 生成了包含完整 `node_modules` 的 `deer-flow-dev-frontend` 镜像。
> 现在改了前端代码，想快速得到一个新的 prod 前端镜像（`next start`），
> 而不想从零重新 `pnpm install` 全部依赖。

---

## 前置检查

确认本机已有 dev 镜像：

```bash
docker images | grep deer-flow-dev-frontend
# 应看到类似：deer-flow-dev-frontend   latest   ...
```

如果没有，先跑一次：

```bash
make docker-start        # 或只 build frontend
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml build frontend
```

---

## 方法一：直接在运行的 dev 容器里 build（最省事）

**前提**：dev 栈正在跑（`deer-flow-frontend` 容器活着）。

dev 容器已经通过 bind mount 同步了你 host 上的代码改动（`../frontend/src` → `/app/frontend/src`），而且容器内已有 `node_modules`，所以直接在里面 build 即可，完全跳过 install。

```bash
# 1. 在运行的 dev 容器内执行 build（利用已有 node_modules）
docker exec deer-flow-frontend sh -c "cd /app/frontend && pnpm build"

# 2. 把当前容器状态保存为 prod 镜像
docker commit deer-flow-frontend deer-flow-frontend:prod-local

# 3. 停掉 dev 栈，改 compose 用新生成的镜像
make docker-stop
```

然后临时改 `docker/docker-compose-dev-backend.yaml`，把 frontend 服务从 `build` 改成直接用镜像：

```yaml
  frontend:
    image: deer-flow-frontend:prod-local
    container_name: deer-flow-frontend
    command: sh -c "cd frontend && pnpm start > /app/logs/frontend.log 2>&1"
    # ... 其余 environment/volumes/networks 保持不变
```

启动：

```bash
docker compose -p deer-flow-dev -f docker/docker-compose-dev-backend.yaml up -d frontend nginx gateway
```

**缺点**：commit 出来的镜像保留了 dev 容器的历史层（比如 `pnpm dev` 的进程残留，虽然你覆盖了 command），不够干净。但对个人开发机完全够用。

**恢复**：改完代码、commit 完、验证通过后，把 `docker-compose-dev-backend.yaml` 改回 `build` 段（保留 `target: prod`），下次 `make docker-start-backend` 会用 Dockerfile 重新走标准流程。

---

## 方法二：用 dev 镜像做一次性 build（更干净）

不依赖 dev 容器是否在跑，从 dev 镜像启动一个临时容器，build 完 commit 成干净镜像。

```bash
# 1. 从 dev 镜像启动一个一次性容器，挂载你改过的前端代码
docker run -d \
  --name deer-flow-frontend-builder \
  -v "$(pwd)/frontend:/app/frontend" \
  deer-flow-dev-frontend:latest \
  tail -f /dev/null

# 2. 在容器内 build（已有 node_modules，秒开）
docker exec deer-flow-frontend-builder sh -c "cd /app/frontend && pnpm build"

# 3. 提交为干净的 prod 镜像
docker commit deer-flow-frontend-builder deer-flow-frontend:prod-local

# 4. 清理临时容器
docker stop deer-flow-frontend-builder
docker rm deer-flow-frontend-builder
```

然后同样改 `docker-compose-dev-backend.yaml` 的 frontend 服务用 `image: deer-flow-frontend:prod-local`，启动即可。

**优点**：不污染正在运行的 dev 容器，build 环境独立。

---

## 方法三：基于 dev 镜像构建干净的 prod 镜像（推荐）

**构建好的prod镜像同样可以作为源镜像来构建新的镜像，不一定非得通过dockerfile原始构建的dev镜像**

dev 镜像和 prod 镜像共享 Dockerfile 里的 `base` 阶段（复制源码），但 dev 的 `install` 是在 `dev` 阶段跑的，prod 的 `install` 是在 `builder` 阶段跑的——两者是独立的层。所以如果你直接 `docker build --target prod`，改源码后 install 会重跑。

最快的做法：**绕过 Dockerfile 多阶段，直接把 dev 镜像当作起点**，只补一个 `pnpm build`。

**前置**：确保 host 上**没有** `frontend/node_modules`，否则会把它复制进镜像，覆盖 dev 镜像里已 install 好的依赖。如果存在，先删掉：
```bash
rm -rf frontend/node_modules
```

```bash
# 基于已有的 dev 镜像，COPY 最新源码后只跑 build，生成干净的 prod 镜像
# 注意：必须先清除 dev 镜像里残留的旧源码（node_modules 除外），
# 否则 host 端已删除的文件（如 nextra 相关文件）不会被 COPY 覆盖掉，
# 导致 build 时引用已删除的文件/依赖而报错。
docker build --platform linux/arm64 \
  --build-arg DEV_IMAGE=deer-flow-dev-frontend:latest \
  -t deer-flow-frontend:prod-from-dev \
  -f - . <<'EOF'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}
# 删除 dev 镜像里的旧源码（保留 node_modules），让下面的 COPY 干净落地
RUN cd /app/frontend && find . -mindepth 1 -maxdepth 1 -not -name node_modules -exec rm -rf {} +
COPY frontend ./frontend
RUN cd /app/frontend && pnpm build
ENV NODE_ENV=production
CMD ["sh", "-c", "cd /app/frontend && pnpm start"]
EOF
```

然后改 `docker-compose-dev-backend.yaml` 的 frontend 服务：

```yaml
  frontend:
    image: deer-flow-frontend:prod-from-dev
    container_name: deer-flow-frontend
    command: sh -c "cd frontend && pnpm start > /app/logs/frontend.log 2>&1"
    # ... 其余 environment/volumes/networks 保持不变
```

启动：

```bash
docker compose -p deer-flow-dev -f docker/docker-compose-dev-backend.yaml up -d frontend nginx gateway
```

**优点**：
- 完全跳过 `pnpm install`，只花时间做 `pnpm build`
- 生成的镜像比 `docker commit`（方法一/二）更干净，没有容器运行时残留层
- 不依赖 dev 容器是否在跑

**以后每次改前端代码，重跑上面那条 `docker build` 命令即可**，install 始终跳过。

### 方法三 A：改了 `package.json` / 引入了新依赖时

如果前端加了新依赖，`pnpm build` 会因为缺少包而失败。最快修复：把 install 也加进内联 Dockerfile。dev 镜像里已有 pnpm store，所以 `pnpm install` 只做链接和解析，不会重新下载包，速度依然很快。

```bash
rm -rf frontend/node_modules

docker build --platform linux/arm64 \
  --build-arg DEV_IMAGE=deer-flow-dev-frontend:latest \
  -t deer-flow-frontend:latest \
  -f - . <<'EOF'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}
RUN cd /app/frontend && find . -mindepth 1 -maxdepth 1 -not -name node_modules -exec rm -rf {} +
COPY frontend ./frontend
RUN cd /app/frontend && pnpm install && SKIP_ENV_VALIDATION=1 pnpm build
ENV NODE_ENV=production
CMD ["sh", "-c", "cd /app/frontend && pnpm start"]
EOF
```

### 方法三 B：把新依赖固化回 dev 镜像（更干净）

如果你频繁改前端，不想每次 prod build 都重复 install，可以先做一个**带新依赖的 dev+ 镜像**：

```bash
# 1. 从旧 dev 镜像启动临时容器，mount 改过的代码（包含新 package.json）
docker run -d \
  --name deer-flow-frontend-devplus \
  -v "$(pwd)/frontend:/app/frontend" \
  deer-flow-dev-frontend:latest \
  tail -f /dev/null

# 2. 在容器内 install 新依赖（利用已有 pnpm store）
docker exec deer-flow-frontend-devplus sh -c "cd /app/frontend && pnpm install --frozen-lockfile"

# 3. commit 为新的 dev+ 镜像
docker commit deer-flow-frontend-devplus deer-flow-dev-frontend:latest

# 4. 清理临时容器
docker stop deer-flow-frontend-devplus && docker rm deer-flow-frontend-devplus
```

以后每次改前端代码，回到**方法三**（只 `COPY` + `pnpm build`），install 再次跳过。

---

## 方法四：直接覆盖 docker-compose 的 build context（最正统）

如果你不想 commit 镜像，也不想改 compose 文件，可以直接覆盖 build 命令，让 `make docker-start-backend` 在 build 时用 `--cache-from` 拉取 dev 镜像的缓存层：

```bash
# 先确保 dev 镜像在本地
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml build frontend

# 然后 build backend-dev 时，显式指定从 dev 镜像拉缓存
cd docker
docker compose \
  -p deer-flow-dev \
  -f docker-compose-dev-backend.yaml \
  build \
  --build-arg BUILDKIT_INLINE_CACHE=1 \
  --cache-from deer-flow-dev-frontend:latest \
  frontend
```

但 **前提是你的 Dockerfile 分层让 install 层可以独立缓存**——当前 Dockerfile 中 `COPY frontend ./frontend` 在 `pnpm install` 之前，任何源码改动都会导致 install 层 cache miss，所以这个方法**对当前 Dockerfile 无效**。如果将来改了 Dockerfile 分层（先 COPY package.json+lock → install → 再 COPY 源码），这才是最佳方法。

---

## 推荐选择

| 场景 | 推荐 |
|---|---|
| 只改了几行前端代码，dev 容器正在跑 | **方法一**（直接在 dev 容器里 build + commit，最快） |
| 改了大量前端代码，想要干净的 prod 镜像 | **方法三**（基于 dev 镜像构建，推荐） |
| dev 容器没在跑，也不想启动它 | **方法三**（基于 dev 镜像构建）或 **方法二**（临时容器 build + commit） |
| 频繁迭代前端，不想每次手动操作 | **方法四**（等 Dockerfile 分层优化后再用） |
---

## 快速重建 Gateway 镜像（基于已有 dev 镜像）

> 适用场景：你在离线机器上通过 `make docker-start-backend` 跑过 dev 栈，
> 生成了包含完整 `.venv` 的 `deer-flow-dev-gateway` 镜像。现在改了后端代码
>（config.yaml、agent 逻辑、工具函数等 Python 文件），想在不触发 `uv sync`
> 联网下载的前提下直接创建新的 gateway 镜像。

### 思路

后端是 Python 解释执行，没有编译构建环节。新镜像只需：
1. 以已有 dev 镜像为基础
2. 覆盖更新后的 `backend/` 源码
3. 用 `uv sync --frozen --offline` 做本地校验（不走网络）

### 前置检查

```bash
docker images | grep deer-flow-dev-gateway
# 应看到: deer-flow-dev-gateway   latest   ...

grep 'uv sync.*--offline' docker/dev-entrypoint.sh
# 应输出: if ! uv sync --all-packages $EXTRAS_FLAGS --frozen --offline; then
```

如果 `dev-entrypoint.sh` 还没有 `--frozen --offline`，先在 host 上修改该文件。

---

#### 方法一：docker commit（最快）

dev 栈正在跑时（`deer-flow-gateway` 容器活着），bind mount 已把 host 上的代码改动
同步进容器（`../backend/` → `/app/backend/`），直接 commit 即可：

```bash
docker commit deer-flow-gateway deer-flow-gateway:local
```

改 `docker-compose-dev-backend.yaml`，gateway 从 `build:` 改为 `image:` 并去掉
`../backend/` 的 bind mount（源码已固化在镜像里）：

```yaml
  gateway:
    image: deer-flow-gateway:local
    volumes:
      - ./dev-entrypoint.sh:/usr/local/bin/dev-entrypoint.sh:ro
      - gateway-venv:/app/backend/.venv
      - ../config.yaml:/app/config.yaml
      - ../extensions_config.json:/app/extensions_config.json
      - ../skills:/app/skills
      - ../logs:/app/logs
      - gateway-uv-cache:/root/.cache/uv
    # environment/networks 保持不变
```

**优点**：零开销，秒生成。
**缺点**：commit 出来的镜像保留容器运行时残留层，不够干净。

---

#### 方法二：内联 Dockerfile 构建（推荐）

构建新镜像时为目标架构重建依赖（需要构建时可联网，或使用内网 PyPI 镜像）。
构建完成后，`.venv` 在镜像内已针对当前架构就绪，启动时由 `gateway-venv` volume 持久化：

```bash
cd "$REPO"
docker build --platform linux/arm64 \
  --build-arg DEV_IMAGE=deer-flow-dev-gateway:latest \
  -t deer-flow-gateway:local \
  -f - . <<'DOCKERFILE'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}

# 清理旧源码和旧 .venv（源机构建，架构不匹配），让下面的 COPY 干净落地
RUN cd /app/backend && find . -mindepth 1 -maxdepth 1 -exec rm -rf {} +

COPY backend ./backend

# 为目标架构重建依赖（需联网；若无外网，用内网 PyPI 镜像或回源机交叉构建）
RUN cd /app/backend && uv sync --all-packages --frozen
DOCKERFILE
```

> `--platform linux/arm64` 按目标机架构调整（x86_64 用 `linux/amd64`），
> 需要和已有 dev 镜像架构一致。

同样改 compose 用 `image:` + 去掉 `../backend/` bind mount：

```yaml
  gateway:
    image: deer-flow-gateway:local
    command: ["sh", "/usr/local/bin/dev-entrypoint.sh"]
    volumes:
      - ./dev-entrypoint.sh:/usr/local/bin/dev-entrypoint.sh:ro
      - gateway-venv:/app/backend/.venv
      - ../config.yaml:/app/config.yaml
      - ../extensions_config.json:/app/extensions_config.json
      - ../skills:/app/skills
      - ../logs:/app/logs
      - gateway-uv-cache:/root/.cache/uv
    # environment/networks 保持不变
```

```bash
cd "$REPO/docker"
docker compose -p deer-flow-dev -f docker-compose-dev-backend.yaml up -d gateway
```

如果已有旧的 `gateway-venv` volume（源机构架，架构不匹配），先删除再启动：

```bash
docker volume rm deer-flow-dev_gateway-venv
```

第一次 `up -d` 时，Docker 会从新镜像的 `.venv` 重新 populate volume，
后续重启时 `dev-entrypoint.sh` 的 `uv sync --frozen --offline` 是纯本地校验，
不触发网络。

**优点**：
- 生成的目标架构 .venv 干净完整
- 容器启动不走网络，复用 docker build 阶段的结果
- 不依赖 dev 容器是否在跑


#### 方法三：不改依赖时跳过 uv sync（更轻量）

如果确认只改了 `.py` 文件，**没有改** `pyproject.toml` 或 `uv.lock`，连 `uv sync`
都可以省略——直接 COPY 源码即可：

```bash
cd "$REPO"
docker build --platform linux/arm64   --build-arg DEV_IMAGE=deer-flow-dev-gateway:latest   -t deer-flow-gateway:local   -f - . <<'DOCKERFILE'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}
RUN cd /app/backend && find . -mindepth 1 -maxdepth 1   -not -name .venv -exec rm -rf {} +
COPY backend ./backend
DOCKERFILE
```

同样改 compose 用 `image:` + 去掉 `../backend/` bind mount。构建只需几秒。

---

### 改了依赖时怎么办

如果修改了 `pyproject.toml` 或 `uv.lock`，`uv sync --frozen --offline` 会因 `.venv`
与锁文件不匹配而失败。离线环境只能回源机重建镜像并重新传输
（参见 `docs/offline-setup.md` §1.3）。

---

### 针对 runtime（生产）镜像

生产部署使用 `docker-compose.yaml` + `runtime` 阶段的镜像（镜像名 `deer-flow-gateway`，
不含 `-dev-`）。跨架构时需要以 **dev 镜像**（`deer-flow-dev-gateway`）为基底，
因为它包含 `build-essential`，能为目标架构编译 native 扩展。

用内联 Dockerfile 构建针对目标架构的运行时镜像：

```bash
cd "$REPO"
docker build --platform linux/arm64 \
  --build-arg DEV_IMAGE=deer-flow-dev-gateway:latest \
  -t deer-flow-gateway:updated \
  -f - . <<'DOCKERFILE'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}

# 清掉旧 .venv（源机构架，native 扩展架构不匹配）和旧源码
RUN cd /app/backend && find . -mindepth 1 -maxdepth 1 \
  -not -name sandbox -exec rm -rf {} +

COPY backend ./backend

# 为目标架构重建依赖（需联网/内网 PyPI 镜像）
RUN cd /app/backend && uv sync --all-packages --frozen

# runtime 不跑 uv sync，用 --no-sync 跳过
CMD ["sh", "-c", "cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]
DOCKERFILE
```

改 `docker-compose.yaml` 的 `image:` 指向 `deer-flow-gateway:updated`
并去掉 `../backend/` bind mount 即可。

> 这个镜像比官方的 runtime 镜像多 ~200MB（因为带 `build-essential`），
> 但功能完全一致。如果要在目标机上做更干净的镜像，可以在构建完成后用
> `docker export` + `docker import` 或重新走源机交叉构建流程
>（`docs/offline-setup.md` §1.3）。

### 推荐选择

| 场景 | 推荐 |
|---|---|
| dev 栈正在跑，只想更新源码 | **方法一**（docker commit，零开销） |
| 想要干净的新镜像 | **方法二**（内联 Dockerfile 构建，推荐） |
| 只改 Python 文件不改依赖 | **方法三**（跳过 uv sync，几秒完成） |
| 改了依赖 | 回源机重建镜像 |


---

## 常见问题

**Q: `pnpm build` 报内存不足（OOM）？**
A: 刀片内存紧张时，给 Node 加上限：
```bash
docker exec deer-flow-frontend sh -c "cd /app/frontend && NODE_OPTIONS='--max-old-space-size=1024' pnpm build"
```

**Q: build 完后怎么切回标准流程？**
A: 把 `docker-compose-dev-backend.yaml` 里的 `image: deer-flow-frontend:prod-local` 改回原来的 `build:` 段即可。或者保留 `image` 但下次 `make docker-start-backend` 前手动 `docker build`。

**Q: 改了 `package.json` 加了新依赖怎么办？**
A: 以上方法都依赖 dev 镜像里已有的 `node_modules`。如果加了新依赖，必须重新 build dev 镜像（`make docker-start` 或 `docker compose ... build frontend`），让新的依赖 install 进去，然后再用上述方法 build prod。
