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

dev 镜像和 prod 镜像共享 Dockerfile 里的 `base` 阶段（复制源码），但 dev 的 `install` 是在 `dev` 阶段跑的，prod 的 `install` 是在 `builder` 阶段跑的——两者是独立的层。所以如果你直接 `docker build --target prod`，改源码后 install 会重跑。

最快的做法：**绕过 Dockerfile 多阶段，直接把 dev 镜像当作起点**，只补一个 `pnpm build`。

**前置**：确保 host 上**没有** `frontend/node_modules`，否则会把它复制进镜像，覆盖 dev 镜像里已 install 好的依赖。如果存在，先删掉：
```bash
rm -rf frontend/node_modules
```

```bash
# 基于已有的 dev 镜像，COPY 最新源码后只跑 build，生成干净的 prod 镜像
docker build --platform linux/arm64 \
  --build-arg DEV_IMAGE=deer-flow-dev-frontend:latest \
  -t deer-flow-frontend:prod-from-dev \
  -f - . <<'EOF'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}
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
  -t deer-flow-frontend:prod-from-dev \
  -f - . <<'EOF'
ARG DEV_IMAGE
FROM ${DEV_IMAGE}
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
