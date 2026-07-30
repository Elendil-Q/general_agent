# Subagent 状态与前端协同同步机制 — 设计文档

日期：2026-07-30
状态：待评审

## 背景与问题

Subagent 状态目前只存在于进程内存的 `AgentRegistry` 中（`backend/packages/harness/deerflow/subagents/agent_registry.py`），
从未注册进 `ThreadState`。前端通过两条路径感知状态：

1. **实时**：`custom` SSE 事件（`task_started`/`task_idle`/`task_expired`/...）→ `onCustomEvent` → 内存态 `SubtasksProvider`
2. **回放**：冷启动时对持久化消息做"考古"（`task` ToolMessage 的 `subagent_status` stamp + `wait_for_tasks` JSON）

由此产生两个 bug：

- **Bug 1 — IDLE 超时不同步**：IDLE TTL（420s）由 `lifecycle.py` 的 `threading.Timer` 在 graph 外触发，
  `task_expired` SSE 事件只在 lead run 的 stream writer 存活时才能送达；而 IDLE 恰恰发生在 turn 结束之后，
  writer 已注销，事件被丢弃（`sse_bridge.py:64-71`），前端永远停在 IDLE。
- **Bug 2 — 重启/切换会话状态错误**：冷启动时无任何服务端事实源，前端故意把 `idle`/`interrupted`
  折叠为 `completed`（`frontend/src/core/tasks/subtask-result.ts:192-199`），把实际仍可 follow-up 的任务标错。

## 目标

建立一条**统一约定**（轻量，不造框架）：任何需要与前端协同更新的 Agent State 内容，都通过
**ThreadState channel → `values` stream → 前端 `AgentThreadState`** 这条既有通路同步。
Subagent 状态是第一个按此约定接入的 channel。

非目标：

- 不实现跨 Gateway 重启的 subagent resume（subagent 执行器与 `InMemorySaver` 仍是进程级）
- 不改造 `AgentRegistry` 的运行时权威地位（registry 为运行时事实源，state channel 为持久镜像）
- 不新建 REST 端点、不新建注册表框架

## 统一约定："UI 协同 State Channel" 四步清单

任何 state 内容要与前端协同，按固定四步接入（与 `todos`/`title` 既有模式一致）：

1. **后端 schema**：在 `thread_state.py` 定义 TypedDict + reducer，挂到 `ThreadState`
2. **中间件接入**：声明 `state_schema`，在 hook（`before_model`/`after_model`）返回 `dict` 写入
   （参照 `TitleMiddleware`）；或由工具经 `Command(update=...)` 写入（参照 `write_todos`）
3. **传输**：无需任何新代码 —— `values` stream 自动携带，checkpointer 自动持久化
4. **前端消费**：`AgentThreadState`（`frontend/src/core/threads/types.ts`）加字段 + 消费方从
   `thread.values.<field>` 读取

该约定将写入 `backend/AGENTS.md` 与 `frontend/AGENTS.md` 作为开发规范。

## 后端设计

### 1. `subagents` state channel（`thread_state.py`）

```python
class SubagentState(TypedDict):
    task_id: str
    subagent_type: str
    status: str            # pending/running/idle/interrupted/completed/failed/cancelled
    idle_expires_at: NotRequired[float | None]  # epoch 秒；仅 idle 时有值
    updated_at: float

def merge_subagents(existing, new):
    # 与 merge_todos 同语义：new is None → 保留 existing；否则整体替换（快照镜像语义）
    if new is None:
        return existing
    return new

class ThreadState(AgentState):
    ...
    subagents: Annotated[dict[str, SubagentState] | None, merge_subagents]
```

镜像采用**整体快照替换**而非增量 merge：registry 是唯一权威，middleware 每次写入全量快照，
避免增量合并带来的状态漂移。

### 2. `SubagentContextMiddleware` 扩展（`subagent_context_middleware.py`）

- 声明 `state_schema = SubagentContextState`（含 `subagents` channel）——用户指定的注册位置
- 新增 `abefore_model(state, runtime)`：
  1. 从 `runtime.context` 取 `thread_id`，读 `agent_registry.list_by_thread(thread_id)` 快照
  2. **Reconcile**（见下节规则）
  3. 与 `state["subagents"]` 比较，有变化才返回 `{"subagents": snapshot}`（避免无意义写放大）
- 既有 `awrap_model_call`（模型侧状态注入）与 `aafter_model`（并发截断 + pending 守卫）保持不变

`idle_expires_at` 由后端在快照生成时计算（`idle_since + DEFAULT_TTL_SECONDS`），
前端无需知道 TTL 常量。

### 3. Reconcile 规则（修复两个 bug 的核心）

每次 `abefore_model` 比较 state 镜像与 registry 实况：

| 情形 | 判定 | 镜像翻转 |
|---|---|---|
| state 有、registry 也有 | 以 registry 为准 | 跟随 registry 状态 |
| state 有（非终态）、registry 无（TTL 回收，同进程） | 最后状态为 IDLE | → `completed` |
| state 有（非终态）、registry 无（进程重启，registry 整体为空） | 最后状态 IDLE | → `completed` |
| 同上 | 最后状态 RUNNING/PENDING/INTERRUPTED | → `cancelled` |

说明：

- **同进程 TTL 回收**与**进程重启**在 registry 视角下都是"条目消失"，无需区分；
  IDLE→completed 与现有 `task_expired` SSE 语义一致（`custom-events.ts:53-61` 本就折叠为 completed）
- `cancelled` 复用现有契约值（"被生命周期终止"，非用户取消但用户视角同为"没跑完"）；
  镜像词表因此全部为 `valid_status_values` 现有值，**契约文件零改动**。前端
  `subtask-result.ts` 的 `STRUCTURED_STATUS_TO_SUBTASK` 已把 cancelled→failed 卡片态
- 设计过程中曾考虑新增 `expired` 状态值，因与 `task_expired` SSE（idle-TTL→completed）语义撞车而放弃

### 4. 生命周期层（`lifecycle.py`）保持不变

TTL Timer 仍触发 `task_expired` SSE 作为 **run 进行中**的快路径；run 间隙的过期由下次 run 的
reconcile 兜底 + 前端本地推导（见下）双保险。三条路径收敛到同一语义（IDLE→completed），互不冲突。

### 5. 同步时序说明（已知限制，可接受）

state channel 只在 graph super-step 边界持久化。turn 结束到 TTL 到期之间的窗口内，
持久化镜像仍显示 IDLE —— 这是正确的，因为此时 subagent 确实仍存活可 follow-up。
真正的"过期翻转"由前端用 `idle_expires_at` 本地推导，不依赖后端写回。

## 前端设计

### 1. `AgentThreadState` 增加 channel 字段（`core/threads/types.ts`）

```ts
subagents?: Record<string, {
  task_id: string;
  subagent_type: string;
  status: "pending" | "running" | "idle" | "interrupted" | "completed" | "failed" | "cancelled";
  idle_expires_at?: number | null;
  updated_at: number;
}> | null;
```

### 2. `SubtasksProvider` 改为"镜像为底、事件为增量"

- **冷启动/会话切换**：从 `thread.values.subagents` 重建 baseline（checkpoint 经
  `fetchStateHistory: { limit: 1 }` 天然可得）。镜像存在时**删除** `subtask-result.ts:192-199`
  的 idle/interrupted→completed 折叠
- **运行中**：既有 `custom` SSE 事件路径不变，作为实时增量覆盖镜像
- **镜像缺失**（旧 thread、subagent 未启用）：回退到现有消息考古逻辑，折叠行为保留（兼容路径）

### 3. IDLE 本地到期推导（Bug 1 的纯前端兜底）

subtask 进入 `idle` 且带 `idle_expires_at` 时，provider 设 `setTimeout` 在到期时刻将卡片翻转为
`completed`（+ `ttlExpired`）。三条路径（SSE 快路径 / reconcile / 本地推导）输出一致，先到先生效，
`shouldKeepPreviousSubtaskStatus` 的现有守卫保证不被回退。

### 4. `cancelled` 状态展示

reconcile 写入的 `cancelled` 经 `STRUCTURED_STATUS_TO_SUBTASK` 折叠为 failed 卡片态，
与 timed_out/cancelled 的既有展示一致，无需新增 UI 路径。

## 数据流总览

```
AgentRegistry (运行时权威, 进程内存)
   │ on_status_change
   ▼
SubagentContextMiddleware.abefore_model  ── 快照 + reconcile ──▶ ThreadState.subagents
                                                                    │ checkpointer 持久化
                                                                    │ values stream
   ┌────────────────────────────────────────────────────────────────┤
   ▼                                                                ▼
custom SSE (task_* 事件, run 内快路径)                    前端 thread.values.subagents
   │                                                                │
   ▼                                                                ▼
SubtasksProvider ◀───────── 镜像为底 + 事件为增量 + idle_expires_at 本地推导
   │
   ▼
SubtaskCard / SubagentList
```

## 错误处理

- **registry 读取失败**：`abefore_model` 捕获异常并返回 `None`（不写镜像），不影响主流程；
  下次 model call 重试
- **镜像与 registry 短暂不一致**：snapshot 替换语义保证下一次写入即自愈
- **旧 thread 无 channel**：前端回退消息考古路径，行为与现状一致

## 测试方案

**后端**（TDD，`backend/tests/`）：

- `merge_subagents` reducer 语义（None 保留 / 快照替换）
- middleware 快照写入：registry 状态变化 → state channel 更新；无变化不写
- reconcile 三规则：TTL 回收（IDLE→completed）、重启孤儿（IDLE→completed、RUNNING/INTERRUPTED→cancelled）
- contract 测试：镜像词表全部为现有 `valid_status_values`，两侧测试无需改动即通过

**前端**（`frontend/tests/unit/`）：

- provider 从 `thread.values.subagents` 冷启动重建（idle 保持 idle，不再折叠 completed）
- 镜像缺失时的消息考古回退路径不变
- `idle_expires_at` 到期本地翻转
- SSE 事件覆盖镜像的优先级（live 事件胜）

## 文档更新（随代码同提交）

- `backend/AGENTS.md`：Middleware Chain #21 条目补充 state channel 职责；"UI 协同 State Channel" 四步约定
- `frontend/AGENTS.md`：thread/streaming 数据流补充 subagents channel 消费方式

## 实施步骤

1. `thread_state.py`：`SubagentState` + `merge_subagents` + `ThreadState.subagents`
2. `subagent_context_middleware.py`：`state_schema` + `abefore_model`（快照 + reconcile）
3. 后端测试
4. 前端 `types.ts` + `SubtasksProvider` 重建逻辑 + idle 本地推导 + 删除冷启动折叠（镜像存在时）
5. 前端测试
6. 文档更新
