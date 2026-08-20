# LangGraph Workflow Subagent Examples 设计

## 背景

为帮助用户在 DeerFlow 中构建自己的 LangGraph workflow subagent，需要在 backend 内提供一套可运行、字段覆盖全面的示例。示例需满足：

1. 展示 LangGraph workflow subagent 可使用的全部关键字段/机制（state 字段、factory 参数、节点/边、中断、工具节点等）。
2. 至少包含 3 个阶段。
3. 中间进度可被捕获。
4. 包含人在回路（HITL）节点。

## 设计决策

### 位置

示例放在 `backend/packages/harness/deerflow/examples/langgraph/` 下：

- 属于 `deerflow-harness` 包的一部分，安装后即可通过 `deerflow.examples.langgraph.*` 导入，无需额外 PYTHONPATH 配置。
- 与 `deerflow.workflows.research_review` 相邻，便于用户对照参考。
- 通过 `config.yaml` 的 `subagents.custom_agents.<name>.workflow` 字段直接引用即可注册为 subagent。

### 示例结构

目录结构：

```text
backend/packages/harness/deerflow/examples/
├── __init__.py
└── langgraph/
    ├── __init__.py
    ├── README.md
    ├── research_approval.py
    └── parallel_research.py
```

### 文件职责

| 文件 | 说明 |
|------|------|
| `research_approval.py` | 核心示例：3 阶段研究审批工作流（研究 → 人工审批 → 报告生成），含 HITL、状态进度、工具节点。 |
| `parallel_research.py` | 进阶示例：展示 `Send` 并行分发、汇总节点、进度字段聚合。 |
| `README.md` | 说明每个示例的用途、如何在 `config.yaml` 中注册、如何触发运行。 |
| `__init__.py` | 导出 factory 函数，方便注册。 |

### `research_approval.py` 阶段设计

3 个阶段：

1. `research`（研究）
   - 接收用户原始请求，使用 `model.bind_tools(tools)` + `ToolNode` 调用文件/沙箱工具收集信息。
   - 每轮迭代更新 `state["stage"] = "research"` 和 `state["progress"]`，捕获中间进度。
   - bounded tool 循环，最多 8 轮，避免无限循环。

2. `approve`（人在回路审批）
   - 调用 `interrupt({"question": ..., "notes": ..., "progress": ...})` 暂停。
   - 用户通过 `POST /api/threads/{tid}/subagents/{task_id}/resume {"resume": {"approved": true, "feedback": "..."}}` 恢复。
   - 根据 resume 值设置 `state["approved"]` 和 `state["feedback"]`。

3. `generate_report` / `revise`（报告生成/修订）
   - 使用条件边 `route_after_approve`：通过 → `generate_report`，驳回 → `revise`。
   - 最终节点产生 terminal `AIMessage`，被 `SubagentExecutor._extract_final_result` 消费。

### State 字段覆盖

`ResearchApprovalState` 继承 `ThreadState`，追加以下字段：

- `stage: str` — 当前阶段（"research" / "approve" / "report" / "revise"）。
- `progress: list[str]` — 每阶段的关键中间产出记录。
- `approved: bool | None` — 审批结果。
- `feedback: str | None` — 审批反馈。
- `artifacts: list[str]` — 研究中发现的文件/输出路径。

这些字段都会随 checkpoint 被自动持久化，因此中间进度可被捕获并在恢复后继续使用。

### Factory 参数使用

`build_graph(*, model, tools, config)` 三个参数全部使用：

- `model`：用于调用 LLM，并通过 `bind_tools` 绑定工具。
- `tools`：通过 `ToolNode` 执行文件/沙箱/MCP 工具。
- `config`：读取 `max_turns` / `timeout_seconds` 等配置，写入 `progress` 元数据。

### 注册方式

在 `config.yaml` 中注册：

```yaml
subagents:
  custom_agents:
    research-approval:
      description: "多阶段研究审批工作流：自动研究 → 人工审批 → 生成报告"
      workflow: "deerflow.examples.langgraph.research_approval:build_graph"
      max_turns: 40
      timeout_seconds: 1200
```

触发方式：

```text
task: 请用 research-approval 研究“2025 年 AI Agent 发展趋势”
```

或使用 `/task:research-approval` 等前端/CLI 形式。

## 备选方案

1. **放在 `backend/examples/langgraph/`**：目录名更直观，但默认不在 `deerflow` 包内，需要额外配置 PYTHONPATH 或安装为独立包，不够“开箱即用”。
2. **只放一个最小示例**：字段覆盖不够全面，用户难以直接扩展出复杂场景。
3. **把示例直接放在 `deerflow/workflows/`**：会混淆“生产 workflow”和“示例”，不便用户区分。

推荐方案兼顾了可运行性和示例清晰性。

## 测试与验证

- 编写 `backend/tests/test_examples_langgraph.py`：
  - 验证 `build_graph` 返回未编译的 `StateGraph`。
  - 验证 interrupt 路径会触发 `INTERRUPTED` 状态。
  - 验证 3 阶段流程完整执行后返回 terminal `AIMessage`。
  - 验证 `state["progress"]` 和 `state["stage"]` 在中间节点被更新。
- 运行 `cd backend && make test` 保证全量测试通过。

## 文档更新

- `backend/AGENTS.md`：在 Workflow Subagents 章节引用 `deerflow.examples.langgraph` 作为官方示例入口。
- `tutorials/`（可选）：新增或更新 tutorial 指引用户如何复制示例并修改。
