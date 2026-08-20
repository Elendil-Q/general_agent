# LangGraph Workflow Subagent Examples

This directory contains runnable LangGraph workflow subagent examples for DeerFlow.

All factories satisfy the workflow-subagent contract:

```python
def build_graph(*, model, tools, config) -> StateGraph: ...
```

They are importable as `deerflow.examples.langgraph.<module>:build_graph` because they live inside the `deerflow-harness` package.

## Examples

### `research_approval.py`

A deterministic, 3-stage pipeline with a human-in-the-loop checkpoint:

1. **research** — bounded model↔tools loop gathers notes and captures progress.
2. **approve** — calls `interrupt()` to pause for human approval/rejection.
3. **generate_report** / **revise** — conditional edge routes to the final report or a revision branch.

State fields demonstrated: `stage`, `progress`, `approved`, `feedback`, `artifacts`, plus the inherited `ThreadState` fields (`messages`, `sandbox`, `thread_data`, ...).

Registration in `config.yaml`:

```yaml
subagents:
  custom_agents:
    research-approval:
      description: "多阶段研究审批工作流：自动研究 → 人工审批 → 生成报告"
      workflow: "deerflow.examples.langgraph.research_approval:build_graph"
      max_turns: 40
      timeout_seconds: 1200
```

### `parallel_research.py`

A 3-stage parallel pipeline:

1. **plan** — splits the request into subtopics.
2. **research_branch** — parallel `Send` branches research each subtopic.
3. **synthesize** — barrier/join node waits for all branches and produces a final report.

Demonstrates `Send` fan-out, state aggregation, and a join node. Progress from each branch is merged into `progress` and `artifacts`.

Registration in `config.yaml`:

```yaml
subagents:
  custom_agents:
    parallel-research:
      description: "并行研究子主题并汇总成综合报告"
      workflow: "deerflow.examples.langgraph.parallel_research:build_graph"
      max_turns: 40
      timeout_seconds: 1200
```

### `cyber_ops/`

A classify → human-confirm → parallel-execute workflow package (`offensive.py` / `defensive.py` / `recon.py` subgraphs plus `graph.py` orchestrator):

1. **classify** — the model decides the request is an offensive / defensive / recon task (may be multiple).
2. **confirm** — `interrupt()` pauses for human approval (approve / reject / free-form input). A rejection is fed back into a re-classification loop (max 3 attempts).
3. **dispatch** — confirmed task types fan out via `Send` to independent single-LLM-node subgraphs, each implemented in its own file.
4. **summarize** — join node aggregates every triggered branch result into the final answer.

Each node appends short `AIMessage`s so intermediate progress reaches the frontend via the executor's `task_running` SSE events. The subgraphs can also run standalone.

Registration in `config.yaml`:

```yaml
subagents:
  custom_agents:
    cyber-ops:
      description: "任务分类 → 人工确认 → 并行执行攻击/防御/侦察分析并汇总"
      workflow: "deerflow.examples.langgraph.cyber_ops:build_graph"
      max_turns: 40
      timeout_seconds: 1200
```

## Usage

After registering the example in `config.yaml`, ask the lead agent to delegate to it:

```text
task: 请使用 research-approval 研究“2025 年 AI Agent 发展趋势”
```

When the `approve` stage is reached, the frontend will show an interrupt prompt. Reply to resume; the graph continues from the checkpoint.

## Extending

To create your own workflow:

1. Copy one of the example files.
2. Extend `ThreadState` with your custom fields (they are checkpointed automatically).
3. Implement `build_graph(*, model, tools, config)` and return an *uncompiled* `StateGraph`.
4. Register it under `subagents.custom_agents.<name>.workflow` in `config.yaml`.

Key constraints:

- The returned graph must be **uncompiled**; the executor attaches its own `InMemorySaver`.
- Forward the full graph state (including `sandbox` and `thread_data`) to `ToolNode.ainvoke` so sandbox/file tools work.
- The final node should produce an `AIMessage` in `state["messages"]`, which `SubagentExecutor._extract_final_result` consumes as the task result.
- Use `interrupt()` for human-in-the-loop pauses; the resume value is returned by `interrupt()` on the next run.

## See Also

- `deerflow.workflows.research_review` — production-style reference workflow.
- `backend/AGENTS.md` — full workflow-subagent contract.
- `tutorials/subagent-chain.md` — subagent/chain usage tutorial.
