# 部署更新手册

> 适用场景：离线目标机已按 [offline-docker-setup.md](offline-docker-setup.md) 完成部署，
> 现在改了代码或依赖，需要更新线上服务。

## 原则

部署只使用仓库自带的 stock 镜像（`backend/Dockerfile` runtime、`frontend/Dockerfile` prod）。
**代码和依赖都烘在镜像里，任何改动都回源机重建镜像**——目标机是离线的，无法补依赖也无法构建。

## 更新流程

```bash
# ── 源机（联网）──
cd "$REPO"
# 1. 改代码 / 依赖（pyproject.toml、uv.lock、package.json、pnpm-lock.yaml）
# 2. 重建镜像
scripts/deploy.sh build
# 3. 导出（与首次部署相同的三个镜像）
docker save deer-flow-gateway deer-flow-frontend nginx:alpine | gzip > deer-flow-images.tar.gz

# ── 传输到目标机 ──

# ── 目标机（离线）──
gunzip -c deer-flow-images.tar.gz | docker load
cd "$REPO"
scripts/deploy.sh start        # 以新镜像重建容器，数据保留在 backend/.deer-flow
```

只改了单个组件时可只导出对应镜像（`docker save deer-flow-gateway` 或 `deer-flow-frontend`）。

## 注意事项

- **改了依赖必须重建镜像**：`uv.lock` / `pnpm-lock.yaml` 变化后，目标机离线环境无法
  `uv sync` / `pnpm install` 补包，唯一路径是源机重建。
- **镜像 ID 会变化**：`docker load` 后旧镜像变 dangling，可定期 `docker image prune` 清理。
- **运行时数据不受影响**：SQLite、会话等在 `$REPO/backend/.deer-flow/`（宿主机挂载），
  重建容器不丢数据。
- **配置改动无需重建镜像**：`config.yaml` / `.env` / `extensions_config.json` 以
  bind mount 挂载进容器，改完 `scripts/deploy.sh start` 重启即可生效。
