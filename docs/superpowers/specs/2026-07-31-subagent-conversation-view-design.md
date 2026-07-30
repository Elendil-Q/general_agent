# Subagent 对话视图设计

日期：2026-07-31
状态：已获用户确认

## 背景与目标

当前 subagent 进度展示分散在两处：`SubtaskCard`（消息流中的卡片，仅显示 task 工具调用信息 + 最新一次工具调用 + 最终结果）和活动栏 `SubagentList`（点击滚动到对应卡片）。subagent 的完整对话历史只存在于易失内存（`SubagentResult.ai_messages` 与 subagent 自带的 `InMemorySaver`），`wait_for_tasks` 收集后即清理，进程重启即失。

目标：

1. `SubtaskCard` 保持现状（仅显示 task 工具调用信息，已实现）。
2. 活动栏中点击 subagent → 跳转到独立的 subagent 对话界面。
3. 该界面形式与主对话界面一致，标题为 `[会话名称]/[subagent简介]`，带返回主对话的按钮。
4. 界面**只读**；subagent 运行中打开时**实时刷新**。
5. 完整对话历史**持久化**，完成后 / 页面刷新 / 服务重启后均可回看。

## 关键结论：必须新增持久化

探索确认当前 subagent 消息无任何持久化路径：

- `SubagentResult.ai_messages` 存于进程内 `AgentRegistry`，`cleanup_background_task`（executor.py:2248）在 `wait_for_tasks` 收集后删除。
- subagent 的 checkpoint 用的是独立的 `InMemorySaver`（executor.py:477-481），进程级，刻意不共享 Gateway 持久 checkpointer（跨事件循环问题）。
- 持久化到磁盘的只有：任务状态（`ThreadState.subagents` mirror channel）、task 工具调用参数、最终结果文本（`wait_for_tasks` ToolMessage）。
- Gateway 无任何按 `task_id` 查询 subagent 消息的 endpoint。

## 方案选型（已定：方案 A）

| 方案 | 结论 |
| --- | --- |
| **A. 独立 subagent 消息 store + 新 REST endpoint（选用）** | 复用 run event store，新增 `category="subagent_message"`；职责清晰、风险低 |
| B. subagent 换共享持久 checkpointer | 跨事件循环 async saver 问题（executor.py:477 刻意拒绝），风险最高，否决 |
| C. mirror channel 携带消息 | lead checkpoint 每步重写导致存储放大，mirror 设计初衷是轻量快照，否决 |

## 后端设计（harness + gateway）

### 1. 持久化：run event store 新增事件类别

- `run_events` 表（`persistence/models/run_event.py:13`）已有通用 `category`（String(16)，`"subagent_message"` 正好 16 字符）与 `event_metadata` JSON 字段——**无需 alembic 迁移**。
- 新事件：`category="subagent_message"`，`content` 为序列化后的 LangChain 消息（AI/Tool），`event_metadata` 携带 `task_id`、`subagent_type`、`description`。
- `RunEventStore.put()` 自动分配 seq（base.py:40），subagent 事件与 lead 事件共享 thread seq 空间，无冲突。
- `RunEventStore` 基类新增查询方法 `list_subagent_messages(thread_id, task_id)`，memory / jsonl / db 三个后端各自实现（按 `category` + `metadata.task_id` 过滤，seq 升序）。
- 生命周期：消息随 lead thread 保留，`delete_by_thread` 一并清除；不再随 `cleanup_background_task` 删除。

### 2. 写入路径：事件总线桥接（避免跨事件循环问题）

subagent 运行在独立事件循环上，db event store 的 SQLAlchemy async engine 绑定主循环——与 checkpointer 相同的跨 loop 约束。因此**不在 executor 内直接写 store**：

1. `SubagentExecutor._aexecute`（及 `_acontinue` / `_aresume`）在累积新消息处（executor.py:1080 附近）向现有 event bus 发射新事件类型 `subagent:message`（携带序列化消息 + task 元信息）。
2. 主循环侧的监听组件（与 `runtime/journal.py` 同级，journal 已在主循环持久化 lead ToolMessage）订阅该事件并写入 run event store（`run_id` 用 parent run_id）。

### 3. 查询 API

新增 `GET /api/threads/{thread_id}/subagents/{task_id}/messages`（挂在 thread_runs 或新 router）：

- 从 event store 取 `category="subagent_message"`、`metadata.task_id` 匹配的事件，seq 升序返回。
- 响应：`{task_id, subagent_type, description, status, messages: [...]}`。`subagent_type`/`description` 取自事件 metadata；`status` 取自 `ThreadState.subagents` mirror（重启后的权威来源）。

### 4. 实时流

复用现有 `task_progress` custom SSE 事件（已携带最新 AIMessage，custom-events.ts:180-193），后端无需改动。

## 前端设计

### 1. 新路由

`/workspace/chats/[thread_id]/subagents/[task_id]/page.tsx`——嵌套在 `chats/[thread_id]` 布局下，保持 thread provider 树与 SSE 连接存活，SPA 内导航时 `task_progress` 事件持续到达。

- 复用主对话的消息渲染组件（message-list 中的 AI/Tool 消息渲染），只读模式（无 composer、无反馈按钮）。
- 标题：`[会话名称]/[subagent简介]`（会话名称取 thread title；简介取 task description）。
- 左上角返回按钮 → 回到 `/workspace/chats/{thread_id}`。

### 2. 数据加载

- 挂载时经 TanStack Query 调新 endpoint 拉取完整历史。
- 任务仍在运行时：订阅 `SubtaskContext` 的 `task_progress` 更新，按消息 id 去重/合并增量追加（`task_progress` 携带的是完整最新 AIMessage 快照，按 id 覆盖即可）。
- 任务完成后自动定格（`task_completed` 后停止追加）。

### 3. 入口改动

- `SubagentList`（activity/subagent-list.tsx:34）的 `handleClick` 从 `scrollIntoView` 改为 `router.push` 到 subagent 路由（`thread_id` 从路由参数/context 获取）。
- `SubtaskCard` 保持不变。

## 测试

- 后端（`backend/tests/`，TDD 强制）：
  - 三个 event store 后端的 `list_subagent_messages` 读写测试。
  - executor 发射 `subagent:message` 事件的测试（`_aexecute` / `_aresume` 路径）。
  - 新 endpoint 的路由测试（含 status 来自 mirror 的断言）。
- 前端（`frontend/tests/unit/`）：
  - 历史加载 + 增量合并（按消息 id 去重）逻辑测试。
  - `SubagentList` 点击导航测试。

## 文档同步（实现时同改动集完成）

- `backend/AGENTS.md`：Subagent System 一节补充消息持久化与新 endpoint。
- `frontend/AGENTS.md`：Data Flow 补充 subagent 对话视图路由与数据来源。
- `README.md`：用户可见的新交互（点击查看 subagent 对话）。

## 明确的非目标（YAGNI）

- 不在 subagent 界面与 subagent 交互（不追加指令、不处理 interrupt 审批——后续可迭代）。
- 不改 subagent checkpointer 隔离设计（`InMemorySaver` 保留）。
- 不做消息保留期/清理策略配置（随 lead thread 生命周期）。
