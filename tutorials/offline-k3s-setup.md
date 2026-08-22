# DeerFlow 离线部署指南（单机 k3s 沙箱模式）

> 面向**单机离线一体机**形态：同一台宿主机上，Docker 跑 DeerFlow 服务栈（gateway / frontend / nginx / provisioner），k3s 跑沙箱 Pod。用轻量 k3s 替代全量 Kubernetes，作为 provisioner（K8s）沙箱模式在离线/内网环境的简便落地方案。
>
> 相关的通用离线 playbook（镜像导出/导入、tiktoken 陷阱、禁止 `--build` 等）见 [offline-docker-setup.md](offline-docker-setup.md)；本文只覆盖 k3s 特有的部分，不重复通用内容。

## 0. 为什么 k3s 可以直接替代全量 K8s

DeerFlow 的沙箱 provisioner（`docker/provisioner/app.py`）对 Kubernetes 的依赖是**刻意最小化**的——只使用 core/v1 稳定 API：

| 用到的 K8s 能力 | 用途 | k3s 支持情况 |
| --- | --- | --- |
| Namespace 读取/创建 | 自动创建 `deer-flow` 命名空间 | ✅ |
| Pod 创建/读取/删除 | 每个沙箱一个 Pod | ✅ |
| Service（NodePort，端口自动分配） | 暴露沙箱 HTTP 端口 | ✅（kube-proxy 原生支持，不依赖 ServiceLB） |
| hostPath Volume（`Directory` / `DirectoryOrCreate`） | 挂载 skills 与 thread 数据 | ✅ 单节点下天然安全 |
| PVC Volume + subPath（可选） | 替代 hostPath 的持久化方案 | ✅ k3s 自带 local-path provisioner，开箱即用 |
| readiness/liveness HTTP 探针 | 沙箱就绪检测 | ✅ |

**完全不使用**：Ingress、LoadBalancer、CRD/Operator、准入 Webhook、NetworkPolicy、PodSecurityPolicy、RBAC 清单、集群 DNS、云厂商 API。因此 k3s 是**完全等价**的运行时，无需任何代码改动。

架构如下：

```
                 ┌─────────────────────── 单台宿主机 ───────────────────────┐
                 │                                                         │
  浏览器 ──▸ nginx:2026 ──▸ gateway:8001 ──┐                               │
                 │                          │ HTTP                         │
                 │                          ▼          ┌───────────────┐   │
                 │                   provisioner:8002 ─▸ k3s API :6443 │   │
                 │                          │          └──────┬────────┘   │
                 │                          │               │ 创建         │
                 │                          │          ┌────▼─────────┐    │
                 │                          └─ NodePort ─▸ 沙箱 Pod(s) │    │
                 │                             (30000-32767)  :8080    │    │
                 │                                     └──────────────┘    │
                 └─────────────────────────────────────────────────────────┘
```

- provisioner 容器经 `host.docker.internal:6443` 访问宿主机上的 k3s API（自签名证书无碍：设置 `K8S_API_SERVER` 时 provisioner 会自动关闭 TLS 校验，见 `app.py`）。
- gateway 容器经 `host.docker.internal:{NodePort}` 直连沙箱 Pod 的 HTTP 服务，不经过集群 DNS。

## 1. 物料清单（源机/联网环境准备）

在有网络的机器上准备以下物料，传输到目标机：

| 物料 | 获取方式 | 备注 |
| --- | --- | --- |
| k3s 二进制 | <https://github.com/k3s-io/k3s/releases> 下载 `k3s` | 按目标机架构选 amd64/arm64 |
| k3s air-gap 镜像包 | 同 release 页下载 `k3s-airgap-images-amd64.tar[.zst]` | k3s 系统组件（coredns、local-path、metrics-server 等） |
| k3s 安装脚本 | `curl -sfL https://get.k3s.io -o install.sh` | 离线安装时本地执行 |
| 沙箱镜像 tar | `docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest && docker save -o all-in-one-sandbox.tar enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest` | x86_64 + arm64 多架构镜像 |
| DeerFlow 服务镜像 tar | 见 [offline-docker-setup.md](offline-docker-setup.md) §1.2/§1.3 | gateway、frontend、nginx |
| provisioner 镜像 tar | `docker compose -f docker/docker-compose.yaml build provisioner && docker save -o provisioner.tar deer-flow-provisioner`（或 compose 自动打的标签） | 本仓库本地构建，无公共镜像；**必须在源机构建**，离线环境不可 `--build` |
| 项目文件夹 | 见 [offline-docker-setup.md](offline-docker-setup.md) §1.4 | 含 `docker/`、`config.yaml` 等 |

**资源估算**：每个沙箱 Pod 的资源为 requests `100m CPU / 256Mi 内存`、limits `1000m CPU / 1Gi 内存 / 500Mi 临时存储`。按预期并发沙箱数叠加，再预留 k3s 自身约 512Mi 内存。

## 2. 目标机离线安装 k3s

以下命令均在目标机（root 或 sudo）执行：

```bash
# 1) 放置二进制与安装脚本
chmod +x k3s install.sh
sudo cp k3s /usr/local/bin/

# 2) 放置 air-gap 镜像包（k3s 启动时自动导入 containerd）
sudo mkdir -p /var/lib/rancher/k3s/agent/images/
sudo cp k3s-airgap-images-amd64.tar.zst /var/lib/rancher/k3s/agent/images/

# 3) 离线安装（跳过下载；--disable traefik 因为 DeerFlow 只用 NodePort，不需要 Ingress）
sudo INSTALL_K3S_SKIP_DOWNLOAD=true ./install.sh --disable traefik

# 4) 验证
sudo k3s kubectl get nodes        # 或等待片刻后: kubectl get nodes（见下一步配置 kubectl）
```

说明：

- **保留默认组件中的 local-path-provisioner 和 metrics-server**：前者供可选的 PVC 模式使用；后者仅被维护脚本 `scripts/sandbox_memory_profile.py`（`kubectl top`）用到，属可选诊断，不影响主流程。
- **ServiceLB（klipper）可留可禁**：NodePort 由 kube-proxy 实现，不依赖 ServiceLB；禁用不影响 DeerFlow。
- k3s 默认使用 containerd 而非 Docker——**docker save/load 进来的镜像 k3s 看不到**，沙箱镜像必须按下节单独导入。

## 3. 预导入沙箱镜像到 k3s

沙箱 Pod 的 `imagePullPolicy` 为 `IfNotPresent`，只要镜像已存在于 k3s 的 containerd 中即不会联网拉取：

```bash
# 在目标机上执行（注意是 k3s ctr，不是 docker load）
sudo k3s ctr images import all-in-one-sandbox.tar

# 确认镜像已在 k3s 命名空间下
sudo k3s ctr images ls | grep all-in-one-sandbox
```

若使用自定义沙箱镜像（见 `docker/provisioner/README.md` 的 Custom sandbox image 一节），同样用 `k3s ctr images import` 导入，并在 compose 中把 `SANDBOX_IMAGE` 改成你的镜像名。

> 提示：`k3s-airgap-images` 包只含 k3s 系统组件，**不含**沙箱镜像，两者都要导入。

## 4. 配置 kubeconfig

k3s 的 admin kubeconfig 在 `/etc/rancher/k3s/k3s.yaml`。provisioner 容器通过挂载 `~/.kube/config` 使用它：

```bash
# 复制为默认 kubeconfig 位置（compose 挂载源）
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
chmod 600 ~/.kube/config

# 宿主机 kubectl 顺手可用（可选）
kubectl get nodes
```

注意：`k3s.yaml` 里的 server 地址是 `https://127.0.0.1:6443`。宿主机上直接用没问题，但 provisioner **容器内**的 127.0.0.1 指容器自身——必须通过下一节的 `K8S_API_SERVER` 改写为 `host.docker.internal`（该改写路径会自动关闭 TLS 校验，k3s 自签名证书无需处理）。k3s 默认 kubeconfig 是 cluster-admin 权限，provisioner 创建命名空间（cluster-scope 操作）不受限。

## 5. 配置 DeerFlow

### 5.1 `docker/docker-compose.yaml` — provisioner 服务

只需把 `K8S_API_SERVER` 的端口从 OrbStack 默认的 `26443` 改成 k3s 的 `6443`，其余保持：

```yaml
  provisioner:
    # ... build / volumes / healthcheck 等保持不变 ...
    environment:
      - K8S_NAMESPACE=deer-flow
      - SANDBOX_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
      - SKILLS_HOST_PATH=${DEER_FLOW_REPO_ROOT}/skills
      - THREADS_HOST_PATH=${DEER_FLOW_HOME}/threads
      - KUBECONFIG_PATH=/root/.kube/config
      - NODE_HOST=host.docker.internal
      - K8S_API_SERVER=https://host.docker.internal:6443   # ← 唯一必改项
```

要点：

- `volumes` 中的 `~/.kube/config:/root/.kube/config:ro` 挂载源必须对应上一步复制好的文件（文件，不是目录）。
- `extra_hosts: "host.docker.internal:host-gateway"` 已存在，Linux 下容器经它访问宿主机的 6443 和 NodePort 端口，无需改动。
- `SKILLS_HOST_PATH` / `THREADS_HOST_PATH` 是**宿主机绝对路径**。单节点 k3s 下 Pod 永远调度在本机，hostPath 与 gateway 容器 bind-mount 的是同一份数据，天然一致。

### 5.2 `config.yaml` — 启用 provisioner 模式

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
  # 注意：provisioner 模式下沙箱镜像由 provisioner 的 SANDBOX_IMAGE 决定，
  # 此处的 sandbox.image 不生效。
```

### 5.3 启动

按 [offline-docker-setup.md](offline-docker-setup.md) §2 的流程加载服务镜像后启动（**不要加 `--build`**）。启动脚本（`scripts/deploy.sh` / `scripts/docker.sh`）会检测 `config.yaml` 的沙箱模式：检测到 provisioner 模式时，自动把 provisioner 服务加入启动列表（其余模式不会启动它）。

## 6. 验证

```bash
# 1) provisioner 健康（容器未暴露宿主机端口，从容器内或经 nginx 验证）
docker exec deer-flow-provisioner curl -s http://localhost:8002/health
# {"status":"ok"}

# 2) provisioner 已连上 k3s 并创建命名空间（日志应有 Created namespace 或 already exists）
docker logs deer-flow-provisioner 2>&1 | head -20
sudo k3s kubectl get ns deer-flow

# 3) 手动创建一个沙箱，验证全链路
docker exec deer-flow-provisioner curl -s -X POST http://localhost:8002/api/sandboxes \
  -H "Content-Type: application/json" \
  -d '{"sandbox_id":"smoke-001","thread_id":"smoke-thread","user_id":"default"}'
# 返回 {"sandbox_id":"smoke-001","sandbox_url":"http://host.docker.internal:3xxxx","status":"Pending"|"Running"}

# 4) 沙箱 Pod 起来了
sudo k3s kubectl get pod,svc -n deer-flow -l sandbox-id=smoke-001

# 5) 从 gateway 容器直连沙箱（NodePort 链路）
SANDBOX_URL=$(docker exec deer-flow-provisioner curl -s http://localhost:8002/api/sandboxes/smoke-001 | jq -r .sandbox_url)
docker exec deer-flow-gateway curl -s $SANDBOX_URL/v1/sandbox

# 6) 清理测试沙箱
docker exec deer-flow-provisioner curl -s -X DELETE http://localhost:8002/api/sandboxes/smoke-001

# 7) 端到端：浏览器打开 nginx 入口（默认 http://localhost:2026），
#    发起一个会调用 bash 工具的对话，确认命令在沙箱 Pod 内执行
```

## 7. 存储：hostPath（默认）vs PVC（可选）

**单节点推荐保持默认 hostPath**：`skills/` 只读挂入 `/mnt/skills`，`$DEER_FLOW_HOME/threads/{thread_id}/user-data` 读写挂入 `/mnt/user-data`，与 gateway 的数据布局天然一致，零额外配置。

若希望沙箱数据由 K8s 统一管理（例如后续扩展为多节点），可改用 PVC——k3s 自带的 local-path provisioner 让 PVC 开箱即绑定：

```bash
# 示例：创建两个 PVC（storageClassName 用 k3s 默认的 local-path）
sudo k3s kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: deer-flow-skills
  namespace: deer-flow
spec:
  accessModes: ["ReadOnlyMany"]
  storageClassName: local-path
  resources: { requests: { storage: 1Gi } }
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: deer-flow-userdata
  namespace: deer-flow
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: local-path
  resources: { requests: { storage: 10Gi } }
EOF
```

然后在 compose 的 provisioner 环境中设置（设置了 PVC 即忽略对应 hostPath）：

```yaml
      - SKILLS_PVC_NAME=deer-flow-skills
      - USERDATA_PVC_NAME=deer-flow-userdata
```

注意：PVC 模式下 user-data 使用 subPath `deer-flow/users/{user_id}/threads/{thread_id}/user-data`；从旧版目录布局迁移见 `docker/provisioner/README.md` 的 "PVC User-Data Upgrade Note"。**多节点集群不要混用 hostPath**（Pod 可能调度到没有数据的节点，`DirectoryOrCreate` 会静默创建空目录）。

## 8. 离线陷阱速查

k3s 模式与 Docker 离线模式共享全部通用陷阱，不再重复——务必对照 [offline-docker-setup.md](offline-docker-setup.md) 的「已知离线陷阱」一节，特别是：

- **tiktoken 离线**：`config.yaml` 中设置 `memory.token_counting: char`（或预打包 tiktoken 缓存），否则启动时联网下载编码文件失败。
- **永不 `--build`**：所有镜像在源机构建、`docker save` 传输、目标机 `docker load`；provisioner 镜像也不例外。
- **沙箱镜像归 k3s 管**：唯一新增陷阱——`docker load` 过的沙箱镜像对 k3s 不可见，必须 `k3s ctr images import`（见 §3）。

## 9. 故障排查

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 沙箱 Pod `ImagePullBackOff` | 镜像只 `docker load` 没导入 k3s containerd | `sudo k3s ctr images import all-in-one-sandbox.tar`（§3） |
| provisioner 日志 "Connection refused" / 连不上 K8s API | `K8S_API_SERVER` 仍是 `26443` 或未设置，容器内访问 `127.0.0.1` 指向自身 | 改为 `https://host.docker.internal:6443`（§5.1） |
| provisioner 报 "Kubeconfig path is a directory" | 挂载源 `~/.kube/config` 不存在，Docker 自动创建了目录 | 先按 §4 复制 k3s.yaml，**删掉误建的目录**再启动 |
| provisioner 创建 namespace 失败（403） | kubeconfig 不是 cluster-admin | 用 `/etc/rancher/k3s/k3s.yaml`（默认 admin），或手工 `kubectl create ns deer-flow` 后确保当前账号有命名空间内 Pod/Service 权限 |
| gateway 访问沙箱 URL 超时 | NodePort 链路不通 | 确认 compose 中 provisioner 与 gateway 都有 `extra_hosts: host.docker.internal:host-gateway`；宿主机防火墙放行 30000-32767 |
| 沙箱内 skills 目录为空或 Pod 创建报 "Unprocessable Entity" | `SKILLS_HOST_PATH` 不是宿主机绝对路径或目录不存在（hostPath type `Directory` 要求必须存在） | 核对 `DEER_FLOW_REPO_ROOT` 环境变量与宿主机路径 |
| 沙箱 Pod 被 evict / `evicted: ephemeral-storage usage exceeds` | 沙箱在容器内（`/mnt/user-data` 之外）写了超过 500Mi 的数据 | 让 agent 把产物写到 `/mnt/user-data`；该限制在 `app.py` 中硬编码，超规格需求需改 provisioner 源码 |
| `kubectl top pod` 无数据 | metrics-server 未运行 | k3s 默认自带，若安装时禁用可忽略（仅影响可选诊断脚本 `scripts/sandbox_memory_profile.py`） |

## 10. 未来可选增强（当前不支持）

以下为简化部署暂不需要、但在更严格场景可能需要的能力，属代码/清单层面的扩展点：

- **`imagePullSecrets`**：provisioner 当前不支持，私有认证 registry 需配置节点级 containerd auth 或小幅扩展 `app.py`。
- **provisioner in-cluster 部署 + RBAC**：当前设计为容器外挂载 admin kubeconfig；若要跑进集群，需自行编写 ServiceAccount + ClusterRole（namespaces）+ Role（`deer-flow` 内 pods/services）清单。
- **Helm chart**：仓库暂无 K8s 部署清单，已在 `docs/upstream-reference-plan.md` 列为未来工作。
