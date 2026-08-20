# 支持 Arize Phoenix Tracing — 设计方案

**日期**：2026-08-20
**目标**：让 DeerFlow 支持 Arize Phoenix 观测（本地自托管 Phoenix，OTLP 导出），并实现按 thread + user 的多轮会话分组。

---

## 1. 背景与关键结论

DeerFlow 现有 tracing 体系（`backend/packages/harness/deerflow/tracing/`）只支持 LangSmith 与 Langfuse，两者都是**基于 LangChain callback handler 的每次运行注入**，通过唯一的 `build_tracing_callbacks()` 汇聚到全部图调用点（lead agent、embedded client、Gateway run worker、subagent executor、model 级 fallback）。

**现代 Phoenix（arize-phoenix-otel ≥0.16）不是 callback 模型**，而是进程级全局 OTel 插桩：

```python
from phoenix.otel import register
register(project_name="deer-flow", endpoint="http://localhost:6006/v1/traces", auto_instrument=True)
```

`register()` 设置全局 TracerProvider 并自动插桩已安装的 openinference 包（含 `openinference-instrumentation-langchain`）。之后所有 LangChain/LangGraph/LLM/tool/MCP 调用自动产生 OTel span，与本仓库现有 LangSmith（`LangChainTracer`）和 Langfuse v4（`langfuse.langchain.CallbackHandler`）并存——因为后两者都不是 OTel 全局 provider，冲突风险低（实现时验证一次即可）。

**因此最干净的做法**：利用 `build_tracing_callbacks()` 作为懒注册挂点——添加 `provider == "phoenix"` 分支调用幂等的 `register_phoenix_tracing()`（不返回 callback）。由于该函数在每个图根调用处都被执行，首次运行时自动完成注册，**所有调用点零改动**即可获得基础 trace。

### 会话 / 用户分组（Phoenix 机制）

- **thread → session**：openinference 的 `OpenInferenceTracer._metadata(run)` 从 `run.extra.metadata`（即 `RunnableConfig.metadata`）读取 `session_id` / `conversation_id` / `thread_id`，映射为 OpenInference 的 `session.id`。→ 只需在现有 4-5 个 metadata 注入点把 `thread_id` 放进 `config["metadata"]`（与 Langfuse 完全同构）。
- **user**：OI tracer **不读** metadata 里的 `user_id`。用户归属必须用 `using_attributes(USER_ID="user.id")` OTel 上下文包裹图调用（`from openinference.instrumentation import using_attributes`）。
- **subagent 线程上下文**：`using_attributes` 基于 OTel context（contextvars），不会跨线程自动传播。需在 `_aexecute` **函数体内**（在后台执行线程中）包裹 `astream`，而非包在 `ThreadPoolExecutor.submit` 外层。

---

## 2. 候选方案对比

| 方案 | 描述 | 结论 |
|---|---|---|
| **A. 现代 Phoenix OTel 全局插桩**（推荐） | `register()` + OI langchain 插桩，挂到 `build_tracing_callbacks()`；metadata 注入 thread_id 做 session；`using_attributes` 做 user | ✅ 推荐。零调用点改动覆盖全链路，与 Langfuse 并存 |
| B. 旧版 Phoenix callback handler | `phoenix.trace.langchain.OpenInferenceTracer` 作为 LangChain callback 注入 | ❌ 需要固定淘汰版 phoenix ≤3.x，丢失现代特性（sessions 等），不建议 |
| C. 进程内嵌 Phoenix（`launch_app`） | 在 Gateway 进程内跑 Phoenix | ❌ 面向单用户 notebook，不适合常驻 server 拓扑 |

---

## 3. 架构与数据流

```
Gateway / Client / Subagent 图调用
   │
   ├─ build_tracing_callbacks()  ← provider=="phoenix" → register_phoenix_tracing()（幂等，不返回 callback）
   │        └─ phoenix.otel.register(project_name, endpoint, auto_instrument=True)
   │                └─ 设置全局 TracerProvider + LangChainInstrumentor 自动插桩
   │
   ├─ inject_*_trace_metadata(config, thread_id=…, user_id=…)   ← config["metadata"]["thread_id"] → session.id
   │
   └─ using_attributes(USER_ID=…) 包裹图调用                    ← user.id（worker / client / subagent _aexecute）
```

- 所有 span 经全局 provider 走 OTLP → `http://localhost:6006/v1/traces` → Phoenix UI（localhost:6006）。
- trace 由 `project_name`（默认 `deer-flow`）分组；多轮会话由 `session.id`（= thread_id）在 Sessions 页分组；用户可在 UI 按 user 过滤。

---

## 4. 工作项

### 4.1 依赖（harness `pyproject.toml`）

- 新增 optional extra `phoenix`：
  - `arize-phoenix-otel>=0.16.0`
  - `openinference-instrumentation-langchain>=0.1.16`
- **懒加载**：仅在 `register_phoenix_tracing()` 内 import；未安装时抛带安装提示的 `RuntimeError`（对齐现有 provider init 失败的报错风格）。
- 理由：phoenix 引入较重 OTel 栈；Langfuse 是硬依赖但 phoenix 作为可选 extra，保持基础安装轻量。现有 `opentelemetry-*` 已在依赖图中（经 langfuse/langsmith 传递）。

### 4.2 配置（`config/tracing_config.py`）

新增 `PhoenixTracingConfig`（env 驱动，同现有 provider）：
- `enabled`：`PHOENIX_TRACING`（布尔，走 `_env_flag_preferred` 语义）
- `endpoint`：`PHOENIX_COLLECTOR_ENDPOINT`，默认 `http://localhost:6006/v1/traces`
- `project_name`：`PHOENIX_PROJECT_NAME`，默认 `deer-flow`
- `is_configured`：`enabled and bool(endpoint)`
- `validate()`：enabled 但 endpoint 为空时抛错（有默认值，实际恒通过）
- `headers`（可选，为将来 hosted/认证预留）：`PHOENIX_HEADERS`（JSON，可选）

接入 `TracingConfig`：
- 增加 `phoenix` 字段
- `explicitly_enabled_providers` / `enabled_providers` / `validate_enabled()` 同步更新
- `get_tracing_config()` / `reset_tracing_config()` 自动覆盖（复用缓存单例 + lock）

### 4.3 新模块 `tracing/phoenix.py`

- `register_phoenix_tracing() -> bool`：
  - `get_tracing_config().phoenix` 未配置 → 返回 `False`
  - 线程锁 + 模块级 `_registered` 标志保证幂等（重复调用直接返回 `True`）
  - 懒 import `phoenix.otel.register`；失败抛安装提示 RuntimeError
  - 调用 `register(project_name=..., endpoint=..., auto_instrument=True)`
  - 返回 `True`
- `shutdown_phoenix_tracing()`（供测试 / 优雅退出）：flush + shutdown span processor（尽力而为，不抛异常）
- 验证点：`register()` 实际签名（`endpoint` 参数名）以安装版本为准；如冲突则退化为手动 `TracerProvider` + `OTLPSpanExporter` + `LangChainInstrumentor().instrument(tracer_provider=...)`。

### 4.4 工厂接入（`tracing/factory.py`）

`build_tracing_callbacks()` 增加 `elif provider == "phoenix":`：
- 调用 `register_phoenix_tracing()`
- **不 append 任何 callback**（全局插桩，避免双倍 span）
- 保持现有 try/except，init 失败抛 `RuntimeError`

结果：lead / client / subagent / model-fallback 四个路径全部自动覆盖，零调用点改动。

### 4.5 元数据与 session/user 分组（`tracing/metadata.py` + 调用点）

- `build_phoenix_trace_metadata(thread_id, user_id, ...) -> dict`：phoenix enabled 时返回 `{"thread_id": ..., "session_id": ..., "user_id": ...}`（其中 `session_id` 与 Langfuse 保持一致语义：subagent 用父 thread_id 归组）；否则返回 `{}`。
- 新增统一入口 `inject_trace_metadata(config, thread_id, user_id, **kw)`：内部按 provider 分别调用 langfuse 与 phoenix 注入（先 langfuse 保持现有行为，再 phoenix），保留 `setdefault`（调用方优先）语义。**调用点只改这一处**，减少漂移：
  - `runtime/runs/worker.py::run_agent`（238-245）
  - `client.py::DeerFlowClient.stream`（614-621）
  - `subagents/executor.py::_aexecute`（899-906）与 resume（1228）
- `user.id`：`using_attributes(USER_ID=..., SESSION_ID=...)` 包裹图调用：
  - `worker.py::run_agent` 的 `agent.ainvoke/astream`
  - `client.py::stream` 的 `agent.astream`
  - `executor.py::_aexecute` 的 `agent.astream`（**在函数体内、后台执行线程中**包裹）
- 注：`inject_langfuse_metadata` 与既有测试保持兼容；`inject_trace_metadata` 内部复用之，不删除。

### 4.6 无需改动

- `models/factory.py::create_chat_model` 的 standalone 调用（MemoryUpdater / title 生成）——全局插桩自动覆盖；phoenix 不返回 callback 故无双 span 问题。

### 4.7 开发工具 / 部署（可选但推荐）

- `docker-compose` 增加 `phoenix` 服务（`arizephoenix/phoenix` 镜像，暴露 6006，持久化 volume），供生产 Docker 栈使用。
- Makefile 增加 `make phoenix`（本地 `uvx arize-phoenix serve`）快捷启动本地观测。

### 4.8 文档（仓库强制策略）

- `backend/AGENTS.md` "Tracing System" 章节：补充 Phoenix provider、env vars 表、metadata 映射表、注入点说明。
- `README.md` / 配置文档：说明 `PHOENIX_TRACING` 等 env 与本地启动方式。

### 4.9 测试（TDD 强制）

扩展 / 新增 `backend/tests/`：
- `test_tracing_factory.py`：`PHOENIX_TRACING=1` 时 provider 出现在 `enabled_providers`；`build_tracing_callbacks()` 触发注册但不返回 callback（monkeypatch `register`）。
- 新 `test_tracing_phoenix.py`：
  - `register_phoenix_tracing()` 幂等（连续调用仅注册一次，monkeypatch `phoenix.otel.register`）
  - 未启用 → 返回 False 且不触发注册
  - `build_phoenix_trace_metadata`：enabled 时含 thread/session/user，未启用时返回 `{}`
  - worker / client / subagent metadata 注入含 `thread_id`（镜像 `test_worker_langfuse_metadata.py` / `test_client_langfuse_metadata.py` 结构）
  - `reset_tracing_config()` 与新 provider 的交互
- 全部用 monkeypatch / 懒加载，保证**不安装 phoenix 也能跑测试**。

### 4.10 验证

- `cd backend && make lint && make format && make test`
- 手动：`uvx arize-phoenix serve` → 跑一次 agent 对话 → localhost:6006 检查 trace 出现、project 为 deer-flow、session.id = thread_id、user 属性正确。

---

## 5. 风险与注意事项

- **OTel 全局 provider 冲突**：本仓库 LangSmith/Langfuse 均为 callback（非全局 provider），风险低；实现时需实际验证 Phoenix 与 Langfuse 并存。
- **`register()` 签名变化**：以安装版本为准，预留退化实现路径（手动 provider + `LangChainInstrumentor().instrument`）。
- **subagent 线程上下文**：`using_attributes` 必须包裹在后台执行线程内，不能包在 `submit()` 外层，否则 user 属性丢失。
- **session 归组粒度**：root span 带 session.id，子 span 继承；subagent 独立 trace 用父 thread_id 归入同一会话（与 Langfuse 现状一致）。

## 6. 范围外

- Phoenix 云端（arize.ai）接入、鉴权 header 细化
- 自定义 span 语义 / 数据脱敏策略
- Phoenix 部署的 HA / 数据生命周期
