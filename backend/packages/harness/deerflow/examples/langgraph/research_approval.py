"""Research → Approval → Report workflow subagent example.

A deterministic, multi-stage LangGraph workflow that demonstrates the full
workflow-subagent contract available in DeerFlow:

* ``build_graph(*, model, tools, config) -> StateGraph`` factory signature;
* a ``ThreadState``-extended state schema with custom progress fields;
* bounded model↔tools research loop using ``ToolNode``;
* intermediate progress captured in checkpointed state fields;
* human-in-the-loop via ``interrupt()`` in the approval node;
* conditional routing after human feedback;
* a terminal ``AIMessage`` produced by the final node (consumed by
  ``SubagentExecutor._extract_final_result``).

Register in ``config.yaml``::

    subagents:
      custom_agents:
        research-approval:
          description: "多阶段研究审批工作流：自动研究 → 人工审批 → 生成报告"
          workflow: "deerflow.examples.langgraph.research_approval:build_graph"
          max_turns: 40
          timeout_seconds: 1200

Trigger via the ``task`` tool, e.g.::

    task(description="研究 2025 年 AI Agent 发展趋势", subagent_type="research-approval")
"""

from typing import NotRequired

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from deerflow.agents.thread_state import ThreadState
from deerflow.tools.builtins.clarification_types import ClarificationInterruptRequest


class ResearchApprovalState(ThreadState):
    """Workflow state extending the executor-injected ``ThreadState``.

    The parent subagent executor seeds every subagent run with a state that
    already contains ``messages``, ``sandbox``, ``thread_data``, etc. Custom
    fields here are checkpointed automatically by LangGraph, so intermediate
    progress survives interruption and resume.
    """

    # Current pipeline stage; updated by each node for progress visibility.
    stage: NotRequired[str | None]
    # Free-form progress log; each node appends a snapshot of its work.
    progress: NotRequired[list[str] | None]
    # Human approval outcome from the interrupt() resume value.
    approved: NotRequired[bool | None]
    # Human feedback text when approval is rejected.
    feedback: NotRequired[str | None]
    # Paths or references to artifacts produced during research.
    artifacts: NotRequired[list[str] | None]


# Maximum number of model↔tools rounds inside the research node. The executor's
# own recursion_limit (config.max_turns) provides an outer cap on the whole graph.
_MAX_RESEARCH_TOOL_ROUNDS = 8


def _last_human_message(state: ResearchApprovalState) -> HumanMessage:
    """Return the most recent HumanMessage from the incoming state."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return msg
    # Fallback: the executor always places the task prompt as a HumanMessage.
    return HumanMessage(content="")


async def _call_model_with_tools(
    model,
    tools: list,
    tool_node: ToolNode | None,
    state: ResearchApprovalState,
    system_prompt: str,
    max_rounds: int = _MAX_RESEARCH_TOOL_ROUNDS,
) -> list:
    """Bounded model↔tools loop.

    Forwards the full ``ThreadState`` to ``ToolNode`` so sandbox/file tools
    receive the injected ``sandbox_id`` and ``thread_data`` paths.
    """
    messages: list = [
        SystemMessage(content=system_prompt),
        _last_human_message(state),
    ]
    new_messages: list = []
    bound_model = model.bind_tools(tools) if tools else model

    for _ in range(max_rounds):
        response = await bound_model.ainvoke(messages)
        new_messages.append(response)
        messages.append(response)
        if not getattr(response, "tool_calls", None):
            break
        if tool_node is None:
            break
        # Pass the full state so ToolRuntime sees sandbox/thread_data.
        tool_result = await tool_node.ainvoke({**state, "messages": messages})
        tool_messages = tool_result["messages"]
        new_messages.extend(tool_messages)
        messages.extend(tool_messages)
    return new_messages


def build_graph(*, model, tools, config) -> StateGraph:  # type: ignore[override]
    """Build the research-approval workflow graph (uncompiled).

    Args:
        model: The chat model resolved by the executor (parent model, no
            tracing). Bind tools to let the research node call file/sandbox tools.
        tools: Policy-filtered tools available to this subagent. When non-empty,
            the model is bound to them and executed via ``ToolNode``. Pass an
            empty list/None for a model-only flow.
        config: The ``SubagentConfig`` (name, max_turns, timeout_seconds, ...).
    """
    graph = StateGraph(ResearchApprovalState)

    tool_list = list(tools or [])
    tool_node = ToolNode(tool_list) if tool_list else None

    async def research(state: ResearchApprovalState) -> dict:
        """Stage 1: gather information using file/sandbox tools."""
        new_messages = await _call_model_with_tools(
            model,
            tool_list,
            tool_node,
            state,
            system_prompt=(
                "You are a research analyst. Use the available file/sandbox tools to inspect the workspace as needed. Collect facts, citations, and file references. Do not write the final answer yet; return concise research notes."
            ),
        )

        notes = new_messages[-1].content if new_messages else ""
        artifacts: list[str] = []
        # Extract any artifact paths mentioned in tool results (best-effort heuristic).
        for msg in new_messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                for marker in ["/mnt/user-data/", "/mnt/acp-workspace/"]:
                    if marker in msg.content:
                        # Keep a simple reference line; real pipelines may parse more carefully.
                        artifacts.append(f"referenced: {msg.content[:200]}")

        progress = (state.get("progress") or []) + [f"Stage 'research' completed: gathered notes ({len(notes)} chars), tool rounds used, artifacts={len(artifacts)}."]

        return {
            "messages": new_messages,
            "stage": "research",
            "progress": progress,
            "artifacts": artifacts,
        }

    def approve(state: ResearchApprovalState) -> dict:
        """Stage 2: pause for human approval of the research notes.

        Uses ``ClarificationInterruptRequest`` with a ``single_choice`` interaction
        so the web frontend renders an approval form and the resume value is the
        selected option string.
        """
        progress = (state.get("progress") or []) + ["Stage 'approve' reached: waiting for human approval via interrupt()."]

        # interrupt() suspends the graph. The resume value is the selected option.
        payload = ClarificationInterruptRequest(
            question="Approve the research notes before generating the final report?",
            interaction="single_choice",
            options=["approve", "reject"],
        )
        decision = interrupt(payload.model_dump(mode="json"))

        approved = decision == "approve"
        feedback = ""
        if not approved:
            if isinstance(decision, str):
                feedback = decision
            elif isinstance(decision, dict):
                feedback = decision.get("feedback", "") or ""

        return {
            "stage": "approve",
            "progress": progress + [f"Approval decision: approved={approved}, feedback={feedback!r}"],
            "approved": approved,
            "feedback": feedback,
        }

    def route_after_approve(state: ResearchApprovalState) -> str:
        """Route to the terminal branch based on the human decision."""
        return "generate_report" if state.get("approved") else "revise"

    async def generate_report(state: ResearchApprovalState) -> dict:
        """Stage 3a (approved): synthesize the final report."""
        task = _last_human_message(state).content
        notes = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                notes = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

        progress = (state.get("progress") or []) + ["Stage 'generate_report' running."]

        response = await model.ainvoke(
            [
                SystemMessage(content="You are a technical writer. Using the approved research notes, write a clear, structured final report in the same language as the request."),
                HumanMessage(content=f"Request: {task}\n\nApproved notes:\n{notes}"),
            ]
        )
        return {
            "messages": [response],
            "stage": "report",
            "progress": progress + ["Stage 'report' completed."],
        }

    async def revise(state: ResearchApprovalState) -> dict:
        """Stage 3b (rejected): revise notes using the human feedback."""
        task = _last_human_message(state).content
        feedback = state.get("feedback") or ""
        notes = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                notes = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

        progress = (state.get("progress") or []) + ["Stage 'revise' running due to rejected approval."]

        response = await model.ainvoke(
            [
                SystemMessage(content="You are a research analyst. The reviewer rejected the prior notes. Use the feedback to improve the research, then produce the final answer."),
                HumanMessage(content=(f"Request: {task}\n\nOriginal notes:\n{notes}\n\nReviewer feedback:\n{feedback}")),
            ]
        )
        return {
            "messages": [response],
            "stage": "revise",
            "progress": progress + ["Stage 'revise' completed."],
        }

    graph.add_node("research", research)
    graph.add_node("approve", approve)
    graph.add_node("generate_report", generate_report)
    graph.add_node("revise", revise)

    graph.add_edge(START, "research")
    graph.add_edge("research", "approve")
    graph.add_conditional_edges(
        "approve",
        route_after_approve,
        {"generate_report": "generate_report", "revise": "revise"},
    )
    graph.add_edge("generate_report", END)
    graph.add_edge("revise", END)

    return graph
