# 自定义 Subagent 与 Chain

## 自定义 Subagent

自定义 subagent 允许你为特定领域任务创建专用的子智能体。Subagent 接收父 agent 委托的任务，在隔离的上下文中执行，完成后返回结果。

### 创建自定义 Subagent

在 `subagents/custom/` 目录下创建一个 `.yaml` 或 `.yml` 文件：

```yaml
# subagents/custom/code-reviewer.yaml
name: code-reviewer
description: "代码审查专家，专注于发现代码缺陷、安全漏洞和性能问题"
system_prompt: |
  你是一个资深代码审查专家。审查代码时关注：
  - 安全漏洞（SQL注入、XSS、认证绕过等）
  - 性能瓶颈（N+1查询、内存泄漏、不必要的计算）
  - 代码规范和最佳实践
  - 可维护性和可读性

  审查完成后给出结构化的报告，按严重程度排列问题。

disallowed_tools:
  - task
  - ask_clarification
  - present_files

skills:
  - code-review    # 显式指定才加载；省略或设为 [] 则不加载任何 skill

model: inherit     # inherit 表示使用父 agent 的模型
max_turns: 80
timeout_seconds: 600
```

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | **是** | 唯一标识符，`task` 工具通过此名称调度 |
| `description` | string | **是** | 用自然语言描述何时应该委托给此 subagent（会出现在父 agent 的 system prompt 中） |
| `system_prompt` | string | 否 | 指导 subagent 行为的系统提示词 |
| `tools` | list | 否 | 工具白名单。`null`（默认）继承父 agent 的所有工具 |
| `disallowed_tools` | list | 否 | 工具黑名单。默认 `["task"]` 防止嵌套委托 |
| `exclusive_tools` | list | 否 | 额外的专属工具路径（如 `"deerflow.tools.custom:MyTool"`） |
| `skills` | list | 否 | Skill 白名单。`null` 或 `[]` 均不加载任何 skill；只在显式指定非空列表时才加载 |
| `model` | string | 否 | 模型名，`"inherit"`（默认）使用父 agent 的模型 |
| `max_turns` | int | 否 | 最大 agent turn 数，默认 50 |
| `timeout_seconds` | int | 否 | 超时秒数，默认 900 |
| `workflow` | string | 否 | 工作流工厂路径（见下方 workflow-subagent 章节） |

### 注册和覆盖

Subagent 的发现和配置覆盖共三层，优先级从高到低：

1. **config.yaml `subagents.agents.<name>`** — 对 built-in subagent 的运行时覆盖（timeout、max_turns、model、skills）
2. **config.yaml `subagents.custom_agents.<name>`** — 在配置文件中内联定义 subagent（优先级高于 YAML 文件）
3. **`subagents/{public,custom}/<name>.yaml`** — 文件系统中的 YAML 定义，`custom/` 目录覆盖 `public/` 目录

示例 — 在 `config.yaml` 中覆盖 built-in 和自定义 subagent 的配置：

```yaml
subagents:
  timeout_seconds: 1800       # 全局默认，仅对 built-in agent 生效
  max_turns: 150              # 全局默认，仅对 built-in agent 生效
  agents:
    general-purpose:
      timeout_seconds: 600    # 覆盖 general-purpose 的超时
      model: gpt-5            # 强制 general-purpose 使用指定模型
    code-reviewer:
      timeout_seconds: 1200
      skills: ["code-review"]
```

### workflow-subagent（高级）

当通用 `create_agent` 模式不够用时，可以通过 `workflow` 字段引用一个自定义 LangGraph 工作流工厂：

```yaml
# subagents/custom/audit-agent.yaml
name: audit-agent
description: "合规审计子智能体，执行严格的多步审计流程"
workflow: "app.audit.workflow:build_audit_graph"
```

工作流工厂签名：

```python
# app/audit/workflow.py
from langgraph.graph import StateGraph
from deerflow.agents.thread_state import ThreadState

def build_graph(*, model, tools, config) -> StateGraph:
    graph = StateGraph(ThreadState)
    # ... 添加节点和边，返回未编译的 StateGraph
    return graph
```

注意：workflow-subagent 不使用 `system_prompt`、`skills` 和 `tools` 过滤 — 这些都由工作流自己完全控制。

### 可用的 built-in subagent

| 名称 | 描述 | 禁用工具 |
|------|------|----------|
| `general-purpose` | 通用子智能体，适合复杂的多步探索+操作任务 | `task`, `present_files` |
| `bash` | 仅 bash 的子智能体 | `task`, `present_files`, `ask_clarification` |

`bash` 仅在沙箱配置允许 host bash 时才可用。`task` 工具在非 workflow subagent 中默认禁用，防止无限嵌套委托。

---

## Chain

Chain 是一种声明式管道，将多个 subagent 按 DAG（有向无环图）编排执行。不同于由模型自主决策调用顺序的 `task` 工具，chain 的**执行顺序是硬编码的** — 由 YAML 中的 `depends_on` 字段定义。

> 💡 **TODO: 基于Langgraph的DAG是否是最终方案？对于需要循环、多智能体间协作、交互的场景是否需要考虑**


### Chain 的执行模型

- 每个节点运行一个 subagent 直到完成
- 没有 `depends_on` 的节点（根节点）从 `START` 并行启动
- 一个节点可以依赖多个上游节点（barrier/join — 所有依赖完成后才启动）
- 终端节点（没有其他节点依赖它）的结果会作为最终的 `AIMessage` 附加到对话线程

### 创建 Chain

在 `chains/custom/` 目录下创建一个 `.yaml` 文件：

```yaml
# chains/custom/research-report.yaml
description: "研究 → 分析 → 报告管道"

nodes:
  web-researcher:
    subagent: general-purpose
    depends_on: []
    prompt: |
      使用 web_search 工具研究以下主题，收集关键事实和来源，返回简洁的总结。

      主题: {input}

  data-analyst:
    subagent: general-purpose
    depends_on: []
    prompt: |
      查找和分析与以下主题相关的本地数据文件，返回你的发现。

      主题: {input}

  synthesizer:
    subagent: general-purpose
    depends_on: [web-researcher, data-analyst]
    prompt: |
      将以下两路研究结果综合成一个连贯的分析报告，解决冲突并突出最重要的洞察。

      网络研究:
      {node_outputs.web-researcher}

      数据分析:
      {node_outputs.data-analyst}

  reporter:
    subagent: general-purpose
    depends_on: [synthesizer]
    prompt: |
      基于以下分析，撰写一份清晰的中文最终报告给用户。

      分析结果:
      {node_outputs.synthesizer}
```

### 使用 Chain

在聊天中输入：

```
/chain:research-report 请研究2025年AI Agent的最新发展趋势
```

### Chain 字段说明

**顶层字段**

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `description` | string | **是** | Chain 的描述（用于自动补全和 API 展示） |
| `nodes` | object | **是** | 节点名 → `ChainNode` 的映射 |

**节点字段（ChainNode）**

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `subagent` | string | **是** | subagent 名称，引用 built-in 或在 registry 中注册的自定义 subagent |
| `depends_on` | list | 否 | 依赖的节点名列表。空列表或省略 = 根节点，可与其他根节点并行执行 |
| `prompt` | string | 否 | 提示词模板。支持 `{input}`（用户原始消息）和 `{node_outputs.<name>}` 占位符。省略时自动拼接上游输出 |

### Chain vs task 工具

| 特性 | Chain | task 工具 |
|------|-------|-----------|
| 执行顺序 | 声明式 DAG，硬编码 | 模型自主决策 |
| 并行 | 根节点自动并行 | 需要模型显式发起多个 task 调用 |
| 确定性 | 每次执行顺序一致 | 模型可自由选择顺序 |
| 适用场景 | 业务流程、标准管道 | 探索性任务、动态委托 |
| 触发方式 | `/chain:<name>` 前缀 | 模型自动调用 `task` 工具 |
