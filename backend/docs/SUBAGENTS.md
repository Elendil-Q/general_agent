# Subagent 运行机制

本文档详细描述 subagent 的运行时架构、执行流程、状态管理和通信机制，用于后续持续开发。Subagent 的配置定义、YAML 文件格式和使用教程请参见 `tutorials/subagent&chain.md`；前端 Subagent Designer 和 REST API CRUD 端点请参见 `backend/AGENTS.md`。

## 架构总览

Subagent 是 lead agent（主智能体）通过 `task` 工具委托的子任务执行单元。整体数据流如下：

```
Lead Agent (model ↔ tools loop)
    │
    ├─ model 生成 AIMessage(tool_calls=[{name: "task", ...}, ...])
    ├─ SubagentLimitMiddleware 截断超限 task 调用 (after_model)
    ├─ ToolNode 执行 task 工具
    │     │
    │     ├─ task_tool(): 解析 subagent_type → 获取 SubagentConfig → 构建 SubagentExecutor
    │     ├─ executor.execute_async(prompt, task_id=tool_call_id)
    │     │     └─ _scheduler_pool (ThreadPoolExecutor, 3 workers)
    │     │           └─ _submit_to_isolated_loop_in_context()
    │     │                 └─ 持久隔离 event loop (daemon thread)
    │     │                       └─ executor._aexecute(task, result_holder)
    │     │                             ├─ _build_initial_state(task)
    │     │                             │     ├─ _load_skills() → default skills (全文注入)
    │     │                             │     ├─ _load_on_demand_skills() → 仅目录
    │     │                             │     ├─ _apply_skill_allowed_tools() → 策略过滤
    │     │                             │     ├─ assemble_deferred_tools() → MCP 延迟加载
    │     │                             │     └─ 组装 SystemMessage + HumanMessage
    │     │                             ├─ _create_agent(tools)
    │     │                             │     ├─ build_subagent_agent() [create_agent 模式]
    │     │                             │     │     ├─ create_chat_model(thinking_enabled=False, attach_tracing=False)
    │     │                             │     │     ├─ build_subagent_runtime_middlewares()
    │     │                             │     │     └─ create_agent(model, tools, middlewares, ThreadState, checkpointer)
    │     │                             │     └─ _create_workflow_agent() [workflow 模式]
    │     │                             │           └─ resolve_variable(config.workflow) → build_graph(model, tools, config)
    │     │                             ├─ agent.astream(state, config, context, stream_mode="values")
    │     │                             │     ├─ 协作取消检查 (cancel_event)
    │     │                             │     ├─ __interrupt__ 检测
    │     │                             │     └─ AI 消息采集 (按 message.id 去重)
    │     │                             └─ SubagentResult (try_set_terminal / try_set_interrupted)
    │     │
    │     └─ 轮询循环 (每 5s)
    │           ├─ get_background_task_result(task_id)
    │           ├─ 状态转换 → SSE 事件 (task_started/running/completed/failed/...)
    │           ├─ INTERRUPTED → 发 task_interrupted SSE 一次，继续轮询
    │           └─ COMPLETED → 返回结果字符串 → cleanup
    │
    ├─ ToolMessage(subagent 结果) 注入 messages
    ├─ ToolErrorHandlingMiddleware 在 ToolMessage 上戳 subagent_status (status_contract.py)
    └─ TokenUsageMiddleware 合并 subagent token 用量到父 AIMessage
```

核心数据模型：

```
SubagentConfig          — 配置（名称/提示词/工具/技能/模型/超时/最大轮次/workflow）
SubagentResult          — 结果容器（任务 ID/状态/结果/错误/AI 消息/token 用量/中断负载）
SubagentStatus(Enum)    — 状态机 (PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT/INTERRUPTED)
SubagentExecutor        — 执行引擎（创建 agent、构造初始状态、流式运行、中断恢复）
SubagentTokenCollector  — Token 采集回调
```

关键源码位置：

| 组件 | 文件 |
|------|------|
| `SubagentConfig` | `packages/harness/deerflow/subagents/config.py` |
| `SubagentExecutor` | `packages/harness/deerflow/subagents/executor.py:353` |
| `SubagentResult` / `SubagentStatus` | `packages/harness/deerflow/subagents/executor.py:52-204` |
| `build_subagent_agent()` | `packages/harness/deerflow/subagents/builder.py:19` |
| `task_tool()` | `packages/harness/deerflow/tools/builtins/task_tool.py:225` |
| `build_subagent_runtime_middlewares()` | `packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py:210` |
| `SubagentLimitMiddleware` | `packages/harness/deerflow/agents/middlewares/subagent_limit_middleware.py` |
| `SubagentTokenCollector` | `packages/harness/deerflow/subagents/token_collector.py` |
| `subagent_status` 契约 | `packages/harness/deerflow/subagents/status_contract.py` |

## Subagent 类型

系统支持三种 subagent 创建方式，在 `SubagentExecutor._create_agent()` 中根据 `config.workflow` 是否设置来决定分支。

### 1. Built-in Subagents（内置）

定义在 `packages/harness/deerflow/subagents/builtins/__init__.py` 的 `BUILTIN_SUBAGENTS` 字典中，程序启动即注册：

| 名称 | 文件 | max_turns | 工具限制 |
|------|------|-----------|----------|
| `general-purpose` | `builtins/general_purpose.py:5` | 150 | 继承所有父工具，禁用 `task` / `present_files`，允许 `ask_clarification`（支持中断） |

### 2. Custom Subagents（create_agent 模式）

通过 `build_subagent_agent()` (`builder.py`) 使用 LangChain 的 `create_agent()` 创建标准的 model-tools 循环 agent。承担 system_prompt、tool policy、skill 加载、middleware 链组装等所有标准逻辑。这是默认且最常用的模式。

### 3. Workflow Subagents（StateGraph 模式）

当 `config.workflow` 指定了 `"module.path:object"` 工厂引用时，dispatch 到 `_create_workflow_agent()`。工厂函数签名为 `build_graph(*, model, tools, config) -> StateGraph`，返回未编译的 `StateGraph`。详见[Workflow Subagent](#workflow-subagent)章节。

## `task` 工具运行流程

`task` 工具是 lead agent 将子任务委托给 subagent 的唯一入口，定义在 `packages/harness/deerflow/tools/builtins/task_tool.py`。

### 1. 工具签名

```python
@tool("task")
async def task_tool(
    description: str,       # 简要描述 (1-2 句)
    prompt: str,            # 详细任务指令
    subagent_type: str,     # "general-purpose" / 自定义名称
    tool_call_id,           # LangChain 注入
    runtime: Runtime,       # LangGraph 注入
) -> str:
```

### 2. 执行阶段

**阶段 A：配置解析**（`task_tool.py:273-339`）

1. `get_subagent_config(subagent_type)` 从注册表中按优先级解析 SubagentConfig
2. 从 `runtime` 提取父 agent 上下文：`sandbox_state`、`thread_data`、`thread_id`、`parent_model`、`trace_id`、`user_id`、`user_role`、`oauth_provider`、`oauth_id`、`run_id`、`clarification_interrupt_enabled`
4. 合并 skill 白名单：如果父 agent 的 `available_skills` 存在，限制 subagent 只能加载父 agent 可见的 skill 子集
5. 解析有效模型名称：`resolve_subagent_model_name(config, parent_model)`

**阶段 B：工具获取**（`task_tool.py:341-360`）

```python
tools = get_available_tools(
    model_name=effective_model,
    groups=parent_tool_groups,  # 继承父 agent 的工具组限制
    subagent_enabled=False,  # 关键：禁止递归嵌套
    app_config=resolved_app_config,
)
```

`subagent_enabled=False` 确保 subagent 不会再有 `task` 工具可用 — 这是防止无限嵌套委托的第一道防线。同时 `SubagentConfig` 的 `disallowed_tools` 默认包含 `["task"]`（第二道防线）。

**阶段 C：创建 Executor**（`task_tool.py:362-384`）

```python
executor = SubagentExecutor(
    config=config,
    tools=tools,
    parent_model=parent_model,
    sandbox_state=sandbox_state,
    thread_data=thread_data,
    thread_id=thread_id,
    trace_id=trace_id,
    user_id=user_id,
    user_role=user_role,
    oauth_provider=oauth_provider,
    oauth_id=oauth_id,
    run_id=run_id,
    clarification_interrupt_enabled=clarification_interrupt_enabled,
    task_id=tool_call_id,  # 用于派生确定性 subagent_thread_id
)
```

`tool_call_id` 作为 `task_id` 传入，executor 据此派生 `subagent_thread_id = f"subagent::{thread_id}::{tool_call_id}"`。这使得中断的 subagent 可以通过 `task_id` 进行 API 寻址恢复。

**阶段 D：启动后台执行**（`task_tool.py:388`）

```python
task_id = executor.execute_async(prompt, task_id=tool_call_id)
```

`execute_async()` 将任务提交到 `_scheduler_pool` (ThreadPoolExecutor, 3 workers)，异步运行在持久隔离 event loop 上。返回 `task_id` 后立即进入轮询。

**阶段 E：轮询循环**（`task_tool.py:411-530`）

每 5 秒轮询 `get_background_task_result(task_id)`：

| 检测条件 | 动作 |
|----------|------|
| `result.status == COMPLETED` | 缓存 token 用量 → 发 `task_completed` SSE → 清理后台任务 → 返回 `"Task Succeeded. Result: ..."` |
| `result.status == FAILED` | 缓存 token 用量 → 发 `task_failed` SSE → 清理 → 返回 `"Task failed. Error: ..."` |
| `result.status == CANCELLED` | 缓存 token 用量 → 发 `task_cancelled` SSE → 清理 → 返回 `"Task cancelled by user."` |
| `result.status == TIMED_OUT` | 缓存 token 用量 → 发 `task_timed_out` SSE → 清理 → 返回 `"Task timed out. Error: ..."` |
| `result.status == INTERRUPTED` | 发 `task_interrupted` SSE **仅一次** → **不返回**，继续轮询等待恢复 |
| `poll_count > max_poll_count` | 超时保护：`(timeout_seconds + 60) / 5` 次轮询后返回 polling timeout |
| 新 AI 消息 | 对每个新消息发 `task_running` SSE（含 message dict、message_index、total_messages） |

**取消处理**（`task_tool.py:531-553`）

当父 agent 被取消时，`task_tool` 捕获 `asyncio.CancelledError`：
1. 调用 `request_cancel_background_task(task_id)` 设置 `cancel_event`（协作取消信号）
2. 使用 `asyncio.shield()` 等待 subagent 到达终止状态以获取最终 token 用量
3. 报告 token 用量到父 `RunJournal`
4. 清理或调度延迟清理
5. 重新 `raise` 传播取消

## 执行引擎 (SubagentExecutor)

`SubagentExecutor` (`executor.py:353`) 是 subagent 运行时的核心编排器。

### 线程池与事件循环架构

```
_scheduler_pool (ThreadPoolExecutor, 3 workers)
  │
  └─ _submit_to_isolated_loop_in_context()
       │  copy_context() 保留 ContextVar 状态
       │
       └─ _get_isolated_subagent_loop()
            │  持久 event loop，运行在 daemon thread 上
            │  通过 threading.Lock 确保单例
            │  atexit 注册 _shutdown_isolated_subagent_loop()
            │
            └─ asyncio.run_coroutine_threadsafe(coro, loop)
```

设计要点：
- `_scheduler_pool` 容纳 3 个并发任务（匹配 `MAX_CONCURRENT_SUBAGENTS`）
- 持久隔离 loop 避免每个任务创建/销毁 event loop 的代价
- `copy_context()` + `context.run()` 确保 ContextVar（如 tracing context）跨线程传递
- `_shutdown_isolated_subagent_loop()` 注册在 `atexit`，进程退出时干净关闭

### 同步与异步执行路径

**`execute(task)`（同步）** — `executor.py:1074`:
1. 检测是否有运行中的 event loop（`asyncio.get_running_loop()`）
2. 有 → 通过 `_execute_in_isolated_loop()` 同步等待持久 loop 上的 future
3. 无 → 直接 `asyncio.run(self._aexecute(task))`

**`execute_async(task, task_id)`（后台）** — `executor.py:1118`:
1. 创建 `SubagentResult(PENDING)`，注册到 `_background_tasks[task_id]` 和 `_subagent_executors[task_id]`
2. 提交到 `_scheduler_pool`：worker 中设置 status=RUNNING，提交到持久 loop，`Future.result(timeout=)` 等待
3. 超时 → `cancel_event.set()` + `try_set_terminal(TIMED_OUT)` + 取消 future

### Checkpointer 隔离

Subagent 的 checkpointer 独立于父 agent 和 Gateway 的 checkpointer：

```python
# executor.py:437-445
self.subagent_thread_id = f"subagent::{thread_id}::{task_id}"
self._checkpointer = InMemorySaver()  # 永不继承父 checkpointer
```

- **为什么不能用父 checkpointer**：父 run 的 checkpointer 可能是同步的（将异步方法会抛异常）或绑定 Gateway 主事件 loop（在隔离 loop 上调用会出错）
- **`InMemorySaver`** 同时实现 sync + async API，循环安全
- **subagent_thread_id** 从 `f"subagent::{thread_id}::{task_id}"` 派生，确保 subagent 的 checkpoint 与父 thread 隔离
- **线程身份分裂**：`configurable.thread_id = self.subagent_thread_id`（subagent 的 checkpointer 使用），`context.thread_id = self.thread_id`（sandbox/file 工具仍写入父 workspace）

```python
# executor.py:902-910
run_config["configurable"] = {"thread_id": self.subagent_thread_id}  # subagent checkpoint
if self.thread_id:
    context["thread_id"] = self.thread_id  # 父 workspace 写入
```

### 初始状态构建

`_build_initial_state(task)` (`executor.py:719`) 返回 `(state, final_tools, deferred_setup)` 三元组：

1. **加载 skills**：
   - `_load_skills()` → 返回 `config.skills` 中列出的默认 skill 列表（SKILL.md 全文将在后续步骤注入）
   - `_load_on_demand_skills()` → 返回 `config.skills_on_demand` 中列出的 on-demand skill 列表（仅用于工具策略和目录渲染）
2. **Skill 工具策略过滤**：`_apply_skill_allowed_tools(default_skills + on_demand_skills)` — 两个列表中的 skill 的 `allowed_tools` 策略从 `_base_tools` 中过滤，按模型名/子 agent 名分组选择
3. **延迟 MCP 工具组装**：`assemble_deferred_tools(filtered_tools, enabled=...)` — 仅在 `tool_search.enabled` 且存在延迟工具时生效
4. **组装消息**：
   - `SystemMessage(system_prompt + skill 全文 + on_demand 目录 + 延迟工具列表)`（单条 SystemMessage，某些 LLM API 不允许多条）
   - `HumanMessage(task)`
5. **沙箱/线程数据透传**

对于 workflow subagent，`_build_workflow_initial_state()` 仅构建 `HumanMessage(task)` + 透传 sandbox/thread_data，不注入 skills 或 tool_search。

### AI 消息采集

`_aexecute()` 的 streaming 循环 (`executor.py:950-996`)：

```python
seen_message_ids: set[str] = {mid for msg in ai_messages if (mid := msg.get("id"))}

async for chunk in agent.astream(state, config=run_config, context=context, stream_mode="values"):
    if result.cancel_event.is_set():  # 协作取消
        ...
    if chunk.get("__interrupt__"):  # 中断检测
        interrupts = chunk["__interrupt__"]
    last_msg = chunk["messages"][-1]
    if isinstance(last_msg, AIMessage):
        if last_msg.id not in seen_message_ids:  # O(1) 去重
            ai_messages.append(last_msg.model_dump())
            seen_message_ids.add(last_msg.id)
```

由于 `stream_mode="values"` 每次 super-step 都 yield 完整状态，相同消息会被重复 yield。`seen_message_ids` 提供 O(1) 去重。

### 结果提取

`_extract_final_result(final_state)` (`executor.py:1327`)：

- 从 `messages` 由后向前查找最后一个 `AIMessage`
- 支持 `str` 和 `list[dict]` (content blocks) 两种内容格式
- list 格式时合并所有 `{"text": "..."}` 块，用 `\n` 连接
- 无 AIMessage 时回退到最后的任意消息，最终回退到 `"No response generated"`

## 生命周期与状态机

### SubagentStatus 枚举

```python
class SubagentStatus(Enum):  # executor.py:52
    PENDING = "pending"  # 初始（execute_async 刚创建 result）
    RUNNING = "running"  # 执行中（astream 循环活跃）
    COMPLETED = "completed"  # 成功结束（AIMessage 结果已提取）
    FAILED = "failed"  # 异常退出（Exception caught）
    CANCELLED = "cancelled"  # 用户取消（cancel_event 触发）
    TIMED_OUT = "timed_out"  # 超时（Future.result(timeout=...) 触发）
    INTERRUPTED = "interrupted"  # 中断暂停（等待 Command(resume=...)）
```

状态属性：

```python
@property
def is_terminal(self) -> bool:
    return self in {COMPLETED, FAILED, CANCELLED, TIMED_OUT}


@property
def is_stopped(self) -> bool:
    """Terminal 或 paused — 轮询循环应停止等待"""
    return self.is_terminal or self is INTERRUPTED
```

### 状态转换图

```
PENDING ──[scheduler worker starts]──→ RUNNING
                                          │
                        ┌─────────────────┼──────────────────┐
                        │                 │                   │
                   [成功完成]      [interrupt() 调用]     [Exception]
                        │                 │                   │
                        ▼                 ▼                   ▼
                   COMPLETED        INTERRUPTED            FAILED
                        │                 │
                        │           [resume_async]
                        │                 │
                        │                 ▼
                        │           RUNNING (cont.)
                        │            │
                        │     ┌──────┼──────┐
                        │     │      │       │
                        │ [完成] [再中断] [Exception]
                        │     │      │       │
                        │     ▼      ▼       ▼
                        │  COMPLETED INTERRUPTED FAILED
                        │
                    ┌───┴───┐
                    │       │
              [cancel_event] [超时]
                    │       │
                    ▼       ▼
               CANCELLED TIMED_OUT
```

### SubagentResult — 线程安全的结果容器

`SubagentResult` (`executor.py:85`) 使用 `threading.Lock` 保护所有可变状态：

```python
@dataclass
class SubagentResult:
    task_id: str
    trace_id: str
    status: SubagentStatus = PENDING
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] = field(default_factory=list)
    token_usage_records: list[dict] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    interrupted_at: datetime | None = None
    interrupts: Any = None
    subagent_thread_id: str | None = None
```

关键方法：

- **`try_set_terminal(status, result=..., error=..., ...)`** — 第一个终端转换写入生效，后续调用被忽略。防止超时线程和正常完成线程之间的竞争。
- **`try_set_interrupted(interrupts, subagent_thread_id, ...)`** — 仅当不是 `is_stopped` 时写入 INTERRUPTED，捕获中断负载和 subagent_thread_id。
- **`try_resume()`** — INTERRUPTED → RUNNING，清除暂停标记和 cancel flag。

### 后台任务生命周期管理

```
execute_async()
  └─ _background_tasks[task_id] = SubagentResult(PENDING)
  └─ _subagent_executors[task_id] = self
  └─ _scheduler_pool.submit(run_task)
       └─ _background_tasks[task_id].status = RUNNING
       └─ _submit_to_isolated_loop(self._aexecute)
       └─ Future.result(timeout=...)  # 阻塞等待执行结束或超时
  return task_id

轮询循环 (task_tool)              后台线程
  ┌─────────────────────┐          ┌──────────────────────────┐
  │ while True:          │          │  agent.astream(...)      │
  │   result = get_bt(t) │ ◄─────── │  检测 cancel_event       │
  │   switch status:     │          │  收集 AI 消息            │
  │     COMPLETED → break│          │  写入 SubagentResult     │
  │     INTERRUPTED →    │          └──────────────────────────┘
  │       继续轮询        │
  │   sleep(5)           │
  └─────────────────────┘

cleanup_background_task(task_id)
  └─ 仅 is_terminal → del _background_tasks[task_id]
  └─ INTERRUPTED → 不删除（等待 resume）
```

## 中间件链

Subagent 使用 `build_subagent_runtime_middlewares()` 组装中间件，与 lead agent 共享基础中间件但有以下差异。

### 共享基础中间件（与 lead agent 相同）

```python
# tool_error_handling_middleware.py:129 (_build_runtime_middlewares)
1.  InputSanitizationMiddleware          # 最外层 wrap_model_call
2.  ToolOutputBudgetMiddleware           # 限制工具输出大小
3.  ThreadDataMiddleware                 # 创建 per-thread 目录
--- UploadsMiddleware 排除 (include_uploads=False)
4.  SandboxMiddleware                    # 获取沙箱
5.  DanglingToolCallMiddleware           # 占位 ToolMessage
6.  LLMErrorHandlingMiddleware           # 标准化 LLM 错误
7.  GuardrailMiddleware (可选)           # 守护策略
8.  SandboxAuditMiddleware               # 安全审计
9.  ToolErrorHandlingMiddleware          # 工具异常转 ToolMessage
```

### Subagent 专属中间件

```python
# tool_error_handling_middleware.py:210 (build_subagent_runtime_middlewares)
10. ViewImageMiddleware (可选)           # 如果模型支持 vision
11. DeferredToolFilterMiddleware (可选)   # MCP 工具延迟加载
12. SafetyFinishReasonMiddleware (可选)   # 提供商安全终止保护
13. ClarificationMiddleware               # 拦截 ask_clarification，必须最后
```

### 与 Lead Agent 的差异

| 中间件 | Lead Agent | Subagent | 原因 |
|--------|-----------|----------|------|
| `UploadsMiddleware` | 包含 | **排除** | Subagent 不需要追踪新上传文件 |
| `DynamicContextMiddleware` | 包含 | **排除** | Subagent 有独立 system prompt |
| `SkillActivationMiddleware` | 包含 | **排除** | Subagent 通过 initial state 加载 skill |
| `SummarizationMiddleware` | 包含 | **排除** | Subagent 有独立 max_turns 限制 |
| `TodoListMiddleware` | 包含 | **排除** | plan mode 仅适用于 lead |
| `TokenUsageMiddleware` | 包含 | **排除** | Subagent token 通过 collector 向上合并 |
| `TitleMiddleware` | 包含 | **排除** | 不需要 |
| `MemoryMiddleware` | 包含 | **排除** | 不需要 |
| `SystemMessageCoalescingMiddleware` | 包含 | **排除** | Subagent 始终只有一条 SystemMessage |
| `SubagentLimitMiddleware` | 包含 | **排除** | Subagent 不能再有 task 工具 |
| `LoopDetectionMiddleware` | 包含 | **排除** | 不需要 |
| `TokenBudgetMiddleware` | 包含 | **排除** | 不需要 |
| `ViewImageMiddleware` | 可选 | **可选** | 条件相同（模型支持 vision） |
| `DeferredToolFilterMiddleware` | 可选 | **可选** | 条件相同（tool_search.enabled） |
| `SafetyFinishReasonMiddleware` | 可选 | **可选** | 条件相同 |
| `ClarificationMiddleware` | 包含 | **包含** | Subagent 也支持 ask_clarification 中断 |

> **注意**: SubagentLimitMiddleware 位于 lead agent 的中间件链上（#21），不在 subagent 上。它通过 `after_model` 在 ToolMessage 处理前截断超限的 `task` tool_calls。

## 工具与技能过滤机制

### 三层工具控制

Subagent 的工具集通过三层控制确定最终可调用的工具列表：

```
get_available_tools(subagent_enabled=False)  → 获取父 agent 可用的所有工具（不含 task）
    │
    └─ _filter_tools(tools, config.tools, config.disallowed_tools)
         │
         ├─ 第一层 (allowlist): 如果 config.tools 不为 None，仅保留白名单中的工具
         │      general-purpose: tools=None -> 不限制（继承全部）
         │
         └─ 第二层 (denylist): 总是排除 config.disallowed_tools 中的工具
                默认 disallowed_tools=["task"]（防止递归嵌套）
                general-purpose 额外排除 present_files

    └─ 第三层 (exclusive_tools): 加载额外工具，不经过父 agent 工具集
         resolve_variable("module.path:ToolName") 逐一解析
         仅当名称不与现有工具冲突时添加
```

exclusive_tools 的加载在 `__init__` 中完成（`executor.py:457-482`），即在初始工具过滤之后立即追加。

### Skill 加载策略

Subagent 支持两种 skill 加载方式，通过 `config.skills` 和 `config.skills_on_demand` 两个独立字段控制：

**默认加载（`skills`）** — `executor.py:750`

列出的 skill 的 `SKILL.md` 全文在 subagent 启动时注入到 `SystemMessage` 中（通过 `_load_skill_messages()`）。适合几乎每次任务都会用到的核心 skill。

**按需加载（`skills_on_demand`）** — `executor.py:751`

仅渲染目录条目（由 `_render_on_demand_skills_section()` 生成 `<available_skills>` 标签），subagent 判断任务匹配后通过 `read_file` 按路径读取 `SKILL.md` 全文。与 lead agent 的 progressive loading 一致。

两者的 `allowed_tools` 策略都会在 `_apply_skill_allowed_tools()` (`executor.py:756`) 中合并处理，确保工具在 subagent 启动时就可调用（避免会话中途工具可用性跳变）。

## 中断与恢复 (Route 甲)

Subagent 支持 `interrupt()` 挂起等待人类输入，lead agent 保持在 `task` 工具中阻塞，用户回复后 subagent 恢复并在**同一 lead turn** 中返回结果。

### 完整流程

```
1. Subagent 运行到 ask_clarification 工具调用
     ↓
2. ClarificationMiddleware (subagent) 检测 structured interaction → interrupt()
     ↓
3. LangGraph yield __interrupt__ chunk → astream 循环捕获
     ↓
4. executor._aexecute: result.try_set_interrupted(interrupts, subagent_thread_id)
     ↓
5. task_tool 轮询检测 INTERRUPTED:
   - 发 task_interrupted SSE 事件 (含 task_id, subagent_thread_id, interrupts)
   - interrupt_announced = True (防止重复发送)
   - 继续轮询 (不返回，lead 保持阻塞)
     ↓
6. 前端收到 task_interrupted → 渲染问询表单
     ↓
7. 用户提交答案 → 前端 POST /api/threads/{thread_id}/subagents/{task_id}/resume
     ↓
8. resume_background_subagent(task_id, resume_value)
   → executor.resume_async(resume_value, task_id)
   → result_holder.try_resume() (INTERRUPTED → RUNNING)
   → _scheduler_pool.submit → _submit_to_isolated_loop → _aresume(resume_value)
     ↓
9. _aresume: 使用相同的子 agent + checkpointer
   → agent.astream(Command(resume=resume_value), ...)
   → 同样的 stream 循环（cancel detection, interrupt detection, AI message collection）
     ↓
10. 完成: try_set_terminal(COMPLETED) → task_tool 轮询检测 → 返回结果
    或再中断: try_set_interrupted → step 5-9 重复
    或失败: try_set_terminal(FAILED)
```

### 关键设计细节

**超时暂停**（`task_tool.py:509-512`）：
```python
# 只有 RUNNING 状态的轮询推进超时计数
if result.status != SubagentStatus.INTERRUPTED:
    poll_count += 1
```
INTERRUPTED 时轮询仍每 5s 检查一次（等待恢复），但不消耗超时配额。这防止了缓慢的人类回复触发执行超时。

**再中断支持**（`task_tool.py:426-429`）：
```python
# 当 subagent 离开 INTERRUPTED 状态时重置 announce 锁
if last_status is SubagentStatus.INTERRUPTED:
    interrupt_announced = False
```
恢复后的 subagent 如果再次调用 `interrupt()`，`task_interrupted` SSE 事件会重新发送。

**取消优先**（`executor.py:1000-1007`）：
```python
if result.cancel_event.is_set():  # 先检查 cancel
    result.try_set_terminal(CANCELLED)
    return result
if interrupts:  # 再检查 interrupt
    result.try_set_interrupted(...)
    return result
```

**Resume 端点验证**（Gateway `thread_runs.py:1058`）：
- 通过 `subagent_thread_id == f"subagent::{thread_id}::{task_id}"` 验证子 agent 属于指定 thread
- 检查 task 确实处于 INTERRUPTED 状态（通过 `get_subagent_interrupt(task_id)`）

**内存存活限制**：INTERRUPTED 的 subagent 状态、executor、checkpointer 全部驻留在进程内存中。Gateway 重启会丢失这些状态（与正常 tool 调用在 flight 时丢失是一致的折衷）。

## Workflow Subagent

Workflow subagent 是 `create_agent` 模式的高级替代，适用于需要严格确定性节点/边流程的场景。

### 配置

在 SubagentConfig 中设置 `workflow: "module.path:object"`，工厂函数签名为：

```python
def build_graph(*, model, tools, config) -> StateGraph:
    """返回未编译的 StateGraph，使用 ThreadState 兼容 schema"""
```

系统使用 `resolve_variable()` 解析工厂引用（与 `config.tools[].use` 共享加载器）。

### 执行流程

```python
# executor.py:527-581 (_create_workflow_agent)
1. resolve_variable(config.workflow) → factory callable
2. build_system_prompt(tools) → 生成工具列表提示（注入到 SystemMessage）
3. model = create_chat_model(name=model_name, thinking_enabled=False, attach_tracing=False)
4. graph = factory(model=model, tools=tools, config=config)
5. compiled = graph.compile(checkpointer=self._checkpointer)
```

### 与 create_agent 模式的关键差异

| 特性 | create_agent | workflow |
|------|-------------|----------|
| `system_prompt` | Executor 注入到 SystemMessage | 工厂自行处理 |
| `skills` / `skills_on_demand` | Executor 加载并注入 | 工厂自行处理 |
| `tools` 过滤 | Executor 通过 `_filter_tools` / `_apply_skill_allowed_tools` | 直接传递，工厂自行决定 |
| 中间件链 | `build_subagent_runtime_middlewares()` 全量装配 | **不适用** — `StateGraph` 无 `add_middleware` |
| Skill 加载 | Executor 在 `_build_initial_state` 中加载 | 工厂自行处理 |
| 初始状态 | SystemMessage + HumanMessage + sandbox/thread_data 透传 | 仅 HumanMessage + sandbox/thread_data 透传 |
| deferred MCP tools | `_build_initial_state` 组装 + 注入提示 | 不处理（工厂自行决定） |

### 状态协议要求

Workflow subagent 必须遵守以下协议以确保 executor 的外层机制正常运行：

1. **`StateGraph` schema 必须兼容 `ThreadState`** — executor 从 `messages` 中提取最终 `AIMessage` 作为结果
2. **`sandbox` 和 `thread_data`** 从父 agent 透传到 `state`，workflow 需要将它们转发给 `ToolNode`：
   ```python
   # 错误：只传 messages，工具看不到沙箱
   ToolNode(tools).ainvoke({"messages": state["messages"]})
   # 正确：转发完整 state
   ToolNode(tools).ainvoke(state)
   ```
3. **返回未编译的 `StateGraph`** — executor 负责 `graph.compile(checkpointer=self._checkpointer)`
4. **可选的 `interrupt()`** — 如果 workflow 调用 `interrupt()`，外层 HITL 机制（Route 甲）自动生效

示例：`packages/harness/deerflow/workflows/research_review.py`

## Token 用量追踪

### SubagentTokenCollector

`SubagentTokenCollector` (`token_collector.py`) 是 `BaseCallbackHandler` 的子类，挂载在 subagent 的 graph 级别 callbacks 上：

```python
# executor.py:862-864
collector_caller = f"subagent:{self.config.name}"
collector = SubagentTokenCollector(caller=collector_caller)
```

`on_llm_end()` 方法从 LLM response 的 `generations[].message.usage_metadata` 中提取 token 计数，按 `run_id` 去重，记录 `{source_run_id, caller, model_name, input_tokens, output_tokens, total_tokens}`。

### 向上合并

**缓存**（`task_tool.py:453`）：
```python
_cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
```
将 `usage` dict 按 `tool_call_id` 存入 `_subagent_usage_cache`。

**合并**（`token_usage_middleware.py:275-319`）：
`TokenUsageMiddleware`（仅 lead agent）在检测到 `task` 工具调用的 `ToolMessage` 时：
1. 调用 `pop_cached_subagent_usage(tool_call_id)` 获取子 agent 用量
2. 从 `messages` 中从后向前查找匹配的 `AIMessage`（通过 `tool_call_id` 匹配）
3. 将 subagent 的 token 用量合并到该 `AIMessage` 的 `usage_metadata` 中

### 取消时的上报

`task_tool.py:531-553` — 捕获 `CancelledError` 后：
1. 使用 `asyncio.shield()` 等待 subagent 到达终止状态
2. 从最终结果中提取 `token_usage_records`
3. 调用 `_report_subagent_usage(runtime, final_result)` 上报到父 `RunJournal`

## 并发控制

### MAX_CONCURRENT_SUBAGENTS = 3

定义在 `executor.py:1452`。线程池容量（`_scheduler_pool` 3 workers）也匹配此数字。

### SubagentLimitMiddleware

位于 lead agent 中间件链 #21 位置（`subagent_limit_middleware.py`），在 `after_model` 中截断超限的 `task` tool_calls：

```python
def _truncate_task_calls(self, state):
    last_msg = messages[-1]  # 最新的 AIMessage
    task_indices = [i for i, tc in enumerate(tool_calls) if tc["name"] == "task"]
    if len(task_indices) <= self.max_concurrent:
        return None  # 无需截断
    # 保留前 max_concurrent 个 task 调用，丢弃剩余
    indices_to_drop = set(task_indices[self.max_concurrent :])
    truncated = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]
    return {"messages": [clone_ai_message_with_tool_calls(last_msg, truncated)]}
```

限制值可在 `[2, 4]` 范围内通过构造函数配置，默认 3。

> **注意**：此中间件仅作用于 lead agent。Subagent 由于 `subagent_enabled=False`，根本不会有 `task` 工具可用，无递归嵌套风险。

## SubagentStatus 契约

Subagent 的状态通过 `ToolMessage.additional_kwargs` 结构化传递给前端：

```python
# status_contract.py
SUBAGENT_STATUS_KEY = "subagent_status"
SUBAGENT_ERROR_KEY = "subagent_error"

SUBAGENT_STATUS_VALUES = (
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "polling_timed_out",
)
```

**后端写入**（`ToolErrorHandlingMiddleware`）：
`make_subagent_additional_kwargs(status, error=...)` 构建 `{"subagent_status": "completed", ...}` 并注入到 ToolMessage。

**后端解析**（`extract_subagent_status(content)`）：
通过前缀匹配推断状态（从 `task_tool.py` 返回的 5 种结果字符串），最具体的前缀优先匹配：

| 前缀 | 状态值 |
|------|--------|
| `"Task Succeeded. Result:"` | `completed` |
| `"Task polling timed out"` | `polling_timed_out` |
| `"Task timed out"` | `timed_out` |
| `"Task cancelled by user"` | `cancelled` |
| `"Task failed."` | `failed` |
| `"Error"` | `failed` |

**契约验证**：`contracts/subagent_status_contract.json` 是前后端共享的测试 fixture，`test_subagent_status_contract.py` 确保两端解析一致。

## 测试覆盖

关键测试文件及覆盖范围：

| 测试文件 | 覆盖范围 |
|----------|----------|
| `test_subagent_executor.py` | Executor 的同步/异步执行路径（2625+ 行） |
| `test_subagent_interrupt_and_resume.py` | 中断 + 恢复完整生命周期 |
| `test_subagent_status_contract.py` | SubagentStatus 契约前后端一致性 |
| `test_subagent_status_semantics.py` | SubagentStatus 枚举语义属性 |
| `test_subagent_limit_middleware.py` | SubagentLimitMiddleware 截断逻辑 |
| `test_subagent_checkpointer_isolation.py` | Checkpointer 隔离验证 |
| `test_subagent_workflow.py` | Workflow subagent 集成 |
| `test_workflow_sandbox_tools.py` | Workflow subagent 沙箱工具路由 |
| `test_subagent_token_collector.py` | Token 采集逻辑 |
| `test_subagent_registry_user_shadow.py` | 注册表 per-user 分层 |
| `test_subagent_clarification.py` | Subagent 中的 ask_clarification |
| `test_subagent_skills_config.py` | Skill 配置加载 |
| `test_subagent_timeout_config.py` | 超时配置 |
| `test_subagent_exclusive_tools.py` | Exclusive tools 加载 |
| `test_subagent_prompt_security.py` | System prompt 注入安全 |
| `test_subagent_deferred_promotion_integration.py` | 延迟 MCP 工具 promotion |
| `test_subagents_user_config.py` | Per-user YAML 存储 |
| `test_subagent_storage.py` | YAML 文件发现与解析 |
