"""cyber_ops workflow: classify → human-confirm → parallel cyber-analysis → summarize.

A DeerFlow workflow subagent that implements the following pipeline:

1. **classify** — the model decides whether the user request is an
   offensive / defensive / recon task (one or more types).
2. **confirm** — ``interrupt()`` pauses for human approval via the frontend
   ``ClarificationInterruptRequest`` form (approve / reject / free-form input).
   A rejection is fed back into a re-classification loop (max
   ``_MAX_CLASSIFICATION_ATTEMPTS``); after that the latest classification is
   used anyway.
3. **dispatch** — confirmed task types fan out in parallel via ``Send`` to
   three independent single-LLM-node subgraphs defined in their own files:
   ``offensive.py`` / ``defensive.py`` / ``recon.py``.
4. **summarize** — a join node aggregates every triggered branch result into
   the final answer (terminal ``AIMessage`` consumed by
   ``SubagentExecutor._extract_final_result``).

Intermediate progress is emitted as short ``AIMessage``s appended to
``messages`` so the executor's ``task_running`` SSE events surface live
stage updates to the frontend.

Register in ``config.yaml``::

    subagents:
      custom_agents:
        cyber-ops:
          description: "任务分类 → 人工确认 → 并行执行攻击/防御/侦察分析并汇总"
          workflow: "deerflow.examples.langgraph.cyber_ops:build_graph"
          max_turns: 40
          timeout_seconds: 1200

Trigger via the ``task`` tool, e.g.::

    task(description="对目标网络进行攻击路径评估", subagent_type="cyber-ops")
"""

from typing import Annotated, NotRequired

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from deerflow.agents.thread_state import ThreadState
from deerflow.examples.langgraph.cyber_ops.defensive import (
    build_graph as build_defensive_graph,
)
from deerflow.examples.langgraph.cyber_ops.offensive import (
    build_graph as build_offensive_graph,
)
from deerflow.examples.langgraph.cyber_ops.recon import (
    build_graph as build_recon_graph,
)
from deerflow.tools.builtins.clarification_types import ClarificationInterruptRequest

#: Task types this workflow can classify a request into.
_TASK_TYPES = ["offensive", "defensive", "recon"]

#: Maximum classify → confirm attempts before the latest classification is used.
_MAX_CLASSIFICATION_ATTEMPTS = 3

# TODO: 按实际业务补充任务分类提示词。
_CLASSIFY_SYSTEM_PROMPT = "Classify the user request into one or more of: offensive, defensive, recon. Return ONLY a comma-separated list, no extra text."

# TODO: 按实际业务补充汇总提示词。
_SUMMARIZE_SYSTEM_PROMPT = "You are a synthesizer. Summarize the branch results into one final answer in the same language as the original request."


def _merge_results(existing: dict[str, str] | None, new: dict[str, str] | None) -> dict[str, str]:
    """Merge parallel branch results into a single dict (last write wins per key)."""
    return {**(existing or {}), **(new or {})}


def _merge_progress(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Merge progress lists, preserving order and deduplicating repeated entries.

    LangGraph checkpoint replay can cause a node to re-emit identical progress
    lines; the reducer keeps the first occurrence of each unique line.
    """
    combined = (existing or []) + (new or [])
    return list(dict.fromkeys(combined))


class CyberOpsState(ThreadState):
    """Workflow state with fields used by the cyber_ops graph."""

    # Original user request.
    request: NotRequired[str | None]
    # Classified task types (subset of _TASK_TYPES).
    selected_types: NotRequired[list[str] | None]
    # Number of classify runs so far.
    classification_attempts: NotRequired[int | None]
    # Whether the human approved the latest classification.
    confirmed: NotRequired[bool | None]
    # Human feedback when a classification was rejected.
    feedback: NotRequired[str | None]
    # Task type a parallel branch is responsible for (seeded via Send).
    task_type: NotRequired[str | None]
    # Per-branch results keyed by task type. Needs a reducer because parallel
    # branches each write a partial dict during the same step.
    branch_results: Annotated[dict[str, str], _merge_results]
    # Current pipeline stage (classify / confirm / dispatch / summarize).
    stage: NotRequired[str | None]
    # Progress log aggregated from all nodes. Needs a reducer because parallel
    # branches each write progress during the same step; deduplicates replays.
    progress: Annotated[list[str] | None, _merge_progress]


def _last_human_message(state: CyberOpsState) -> HumanMessage:
    """Return the most recent HumanMessage from the incoming state."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return msg
    return HumanMessage(content="")


def _make_branch_runner(task_type: str, subgraph):
    """Create the branch adapter node that runs a compiled subgraph.

    The node reads the original ``request`` from the ``Send``-seeded partial
    state, invokes the subgraph, and merges the subgraph's ``output`` back into
    ``branch_results`` plus a short progress/AIMessage for SSE visibility.
    """

    async def run_branch(state: CyberOpsState) -> dict:
        request = state.get("request") or ""
        result = await subgraph.ainvoke(
            {
                "input": request,
                "messages": state.get("messages", []),
                "sandbox": state.get("sandbox"),
                "thread_data": state.get("thread_data"),
            }
        )
        output = result.get("output") or ""
        if not isinstance(output, str):
            output = str(output)
        return {
            "branch_results": {task_type: output},
            "progress": [f"Branch '{task_type}' completed ({len(output)} chars)."],
            "messages": [AIMessage(content=f"[{task_type}] 分析完成。")],
        }

    return run_branch


def build_graph(*, model, tools, config) -> StateGraph:  # type: ignore[override]
    """Build the cyber_ops workflow graph (uncompiled).

    Args:
        model: The chat model resolved by the executor (no tracing).
        tools: Policy-filtered tools available to this subagent (unused; all
            nodes are model-only).
        config: ``SubagentConfig`` (name, max_turns, timeout_seconds, ...).
    """
    # Compile the three single-LLM-node subgraphs; each branch invokes one.
    offensive_graph = build_offensive_graph(model=model, tools=[], config=config).compile()
    defensive_graph = build_defensive_graph(model=model, tools=[], config=config).compile()
    recon_graph = build_recon_graph(model=model, tools=[], config=config).compile()

    async def classify(state: CyberOpsState) -> dict:
        """Stage 1: decide which task types the request belongs to."""
        request = _last_human_message(state).content
        feedback = state.get("feedback") or ""

        context = f"User request: {request}"
        if feedback:
            context += f"\n\nUser feedback on the previous classification (it was rejected): {feedback}"

        response = await model.ainvoke(
            [
                SystemMessage(content=_CLASSIFY_SYSTEM_PROMPT),
                HumanMessage(content=context),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        # Normalize "offensive, defensive" / "offensive\n defensive" → list.
        types = [t.strip().lower() for t in content.replace(",", "\n").split("\n")]
        types = [t for t in types if t in _TASK_TYPES]
        if not types:
            # Fallback: unparseable output → assume all task types.
            types = list(_TASK_TYPES)

        attempts = (state.get("classification_attempts") or 0) + 1
        progress = (state.get("progress") or []) + [f"Stage 'classify' (attempt {attempts}): {types}."]

        return {
            "request": str(request),
            "selected_types": types,
            "classification_attempts": attempts,
            "stage": "classify",
            "progress": progress,
            "messages": [AIMessage(content=f"任务分类结果：{', '.join(types)}")],
        }

    def confirm(state: CyberOpsState) -> dict:
        """Stage 2: pause for human approval of the classification.

        Uses ``ClarificationInterruptRequest`` with a ``single_choice``
        interaction so the web frontend renders an approval form and the resume
        value is the selected option string (or free-form "other" input).
        """
        types = state.get("selected_types") or list(_TASK_TYPES)

        # interrupt() suspends the graph; the resume value is the human's answer.
        payload = ClarificationInterruptRequest(
            question=f"对任务类型的判断为：{', '.join(types)}。是否确认？",
            interaction="single_choice",
            options=["approve", "reject"],
        )
        decision = interrupt(payload.model_dump(mode="json"))

        confirmed = decision == "approve"
        feedback = ""
        if not confirmed:
            if isinstance(decision, str):
                feedback = decision
            elif isinstance(decision, dict):
                feedback = decision.get("feedback", "") or ""

        progress = (state.get("progress") or []) + [f"Confirm decision: confirmed={confirmed}, feedback={feedback!r}."]
        if not confirmed and (state.get("classification_attempts") or 0) >= _MAX_CLASSIFICATION_ATTEMPTS:
            progress += ["已连续被拒绝，达到最大重试次数，将按当前分类继续执行。"]

        status = "人工确认通过。" if confirmed else f"人工拒绝：{feedback}"
        return {
            "confirmed": confirmed,
            "feedback": feedback,
            "stage": "confirm",
            "progress": progress,
            "messages": [AIMessage(content=status)],
        }

    def route_after_confirm(state: CyberOpsState) -> str:
        """Route based on the human decision: approved → dispatch; else reclassify until exhausted."""
        if state.get("confirmed"):
            return "dispatch"
        attempts = state.get("classification_attempts") or 0
        return "classify" if attempts < _MAX_CLASSIFICATION_ATTEMPTS else "dispatch"

    def dispatch(state: CyberOpsState) -> dict:
        """Stage 3: mark the transition to parallel execution (routed to via Send)."""
        return {"stage": "dispatch"}

    def route_to_branches(state: CyberOpsState) -> list[Send]:
        """Fan out one ``Send`` per confirmed task type to its branch node."""
        types = state.get("selected_types") or []
        return [
            Send(
                f"{task_type}_branch",
                {
                    # Minimal ThreadState-compatible dict plus the task type.
                    "messages": state.get("messages", []),
                    "sandbox": state.get("sandbox"),
                    "thread_data": state.get("thread_data"),
                    "task_type": task_type,
                    "request": state.get("request"),
                },
            )
            for task_type in types
        ]

    async def summarize(state: CyberOpsState) -> dict:
        """Stage 4: join all branch results into the terminal final answer."""
        request = state.get("request") or ""
        branch_results = state.get("branch_results") or {}

        progress = (state.get("progress") or []) + [f"Stage 'summarize' reached: joining {len(branch_results)} branch results."]

        sections = "\n\n".join(f"## {name}\n{result}" for name, result in branch_results.items())

        response = await model.ainvoke(
            [
                SystemMessage(content=_SUMMARIZE_SYSTEM_PROMPT),
                HumanMessage(content=f"Original request: {request}\n\nBranch results:\n\n{sections}"),
            ]
        )
        return {
            "messages": [response],
            "stage": "summarize",
            "progress": progress + ["Stage 'summarize' completed."],
        }

    graph = StateGraph(CyberOpsState)

    graph.add_node("classify", classify)
    graph.add_node("confirm", confirm)
    graph.add_node("dispatch", dispatch)
    graph.add_node("offensive_branch", _make_branch_runner("offensive", offensive_graph))
    graph.add_node("defensive_branch", _make_branch_runner("defensive", defensive_graph))
    graph.add_node("recon_branch", _make_branch_runner("recon", recon_graph))
    graph.add_node("summarize", summarize)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "confirm")
    graph.add_conditional_edges(
        "confirm",
        route_after_confirm,
        {"classify": "classify", "dispatch": "dispatch"},
    )
    graph.add_conditional_edges(
        "dispatch",
        route_to_branches,
        ["offensive_branch", "defensive_branch", "recon_branch"],
    )
    graph.add_edge("offensive_branch", "summarize")
    graph.add_edge("defensive_branch", "summarize")
    graph.add_edge("recon_branch", "summarize")
    graph.add_edge("summarize", END)

    return graph
