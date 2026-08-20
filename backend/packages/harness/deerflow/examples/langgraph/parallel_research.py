"""Parallel research → Synthesis workflow subagent example.

Demonstrates advanced LangGraph patterns in a DeerFlow workflow subagent:

* ``build_graph(*, model, tools, config) -> StateGraph`` factory;
* ``Send`` parallel fan-out to multiple research sub-graphs;
* a barrier/join node that waits for all parallel branches;
* aggregated ``progress`` and ``artifacts`` captured per branch;
* a final terminal ``AIMessage`` with the synthesized report.

Register in ``config.yaml``::

    subagents:
      custom_agents:
        parallel-research:
          description: "并行研究子主题并汇总成综合报告"
          workflow: "deerflow.examples.langgraph.parallel_research:build_graph"
          max_turns: 40
          timeout_seconds: 1200

Trigger via the ``task`` tool, e.g.::

    task(description="并行研究 AI Agent 在医疗、金融、教育领域的应用", subagent_type="parallel-research")
"""

from typing import Annotated, NotRequired

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Send

from deerflow.agents.thread_state import ThreadState


def _merge_progress(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Merge progress lists, preserving order and deduplicating repeated entries.

    LangGraph checkpoint replay can cause a node to re-emit identical progress
    lines; the reducer keeps the first occurrence of each unique line.
    """
    combined = (existing or []) + (new or [])
    return list(dict.fromkeys(combined))


class ParallelResearchState(ThreadState):
    """Workflow state with fields used by the parallel research graph."""

    # Original user request.
    request: NotRequired[str | None]
    # Sub-topics to research in parallel.
    subtopics: NotRequired[list[str] | None]
    # Current pipeline stage (plan / research / synthesis).
    stage: NotRequired[str | None]
    # Per-branch results keyed by subtopic. Needs a reducer because parallel
    # branches each write a partial dict during the same step.
    branch_results: Annotated[dict[str, str], lambda old, new: {**(old or {}), **(new or {})}]
    # Progress log aggregated from all branches. Needs a reducer because parallel
    # branches each write progress during the same step; deduplicates replays.
    progress: Annotated[list[str] | None, _merge_progress]


# Maximum number of model↔tools rounds inside a single research branch.
_MAX_BRANCH_TOOL_ROUNDS = 4


def _last_human_message(state: ParallelResearchState) -> HumanMessage:
    """Return the most recent HumanMessage from the incoming state."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return msg
    return HumanMessage(content="")


async def _call_model_with_tools(
    model,
    tools: list,
    tool_node: ToolNode | None,
    state: ParallelResearchState,
    system_prompt: str,
    max_rounds: int = _MAX_BRANCH_TOOL_ROUNDS,
) -> list:
    """Bounded model↔tools loop forwarding the full ThreadState to ToolNode."""
    messages: list = [SystemMessage(content=system_prompt)]
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
        tool_result = await tool_node.ainvoke({**state, "messages": messages})
        tool_messages = tool_result["messages"]
        new_messages.extend(tool_messages)
        messages.extend(tool_messages)
    return new_messages


def build_graph(*, model, tools, config) -> StateGraph:  # type: ignore[override]
    """Build the parallel-research workflow graph (uncompiled).

    Args:
        model: Resolved chat model for the subagent.
        tools: Policy-filtered tools available to the workflow.
        config: ``SubagentConfig`` for this subagent.
    """
    graph = StateGraph(ParallelResearchState)

    tool_list = list(tools or [])
    tool_node = ToolNode(tool_list) if tool_list else None

    async def plan(state: ParallelResearchState) -> dict:
        """Stage 1: read the request and split it into parallel subtopics."""
        request = _last_human_message(state).content

        response = await model.ainvoke(
            [
                SystemMessage(content=("You are a planning assistant. Given a user request, identify 2-4 distinct subtopics that can be researched in parallel. Return ONLY a comma-separated list of subtopic names, no extra text.")),
                HumanMessage(content=str(request)),
            ]
        )

        content = response.content if isinstance(response.content, str) else str(response.content)
        # Split by commas or newlines; strip numbering/bullets.
        subtopics = [line.strip(" -•0123456789).") for line in content.replace(",", "\n").split("\n") if line.strip(" -•0123456789).")]

        progress = [f"Stage 'plan' completed: split request into {len(subtopics)} subtopics: {subtopics}."]
        return {
            "request": str(request),
            "subtopics": subtopics,
            "branch_results": {},
            "stage": "plan",
            "progress": progress,
        }

    def route_to_branches(state: ParallelResearchState) -> list[Send]:
        """Fan out one ``Send`` per subtopic to the ``research_branch`` node."""
        subtopics = state.get("subtopics") or []
        return [
            Send(
                "research_branch",
                {
                    # Pass a minimal ThreadState-compatible dict plus the subtopic.
                    "messages": state.get("messages", []),
                    "sandbox": state.get("sandbox"),
                    "thread_data": state.get("thread_data"),
                    "subtopic": subtopic,
                    "request": state.get("request"),
                },
            )
            for subtopic in subtopics
        ]

    async def research_branch(state: ParallelResearchState) -> dict:
        """Parallel branch: research a single subtopic.

        Receives a partial state from ``Send`` containing at least ``subtopic``
        and the original request. It returns updates that are merged into the
        main graph state via the state reducer.
        """
        subtopic = state.get("subtopic") or "unknown"
        request = state.get("request") or ""

        new_messages = await _call_model_with_tools(
            model,
            tool_list,
            tool_node,
            state,
            system_prompt=(f"You are a research analyst focused on the subtopic '{subtopic}'. The overall request is: {request}. Use available tools if needed, then return concise research notes for this subtopic only."),
        )

        summary = new_messages[-1].content if new_messages else ""
        if not isinstance(summary, str):
            summary = str(summary)

        artifacts: list[str] = []
        for msg in new_messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                for marker in ["/mnt/user-data/", "/mnt/acp-workspace/"]:
                    if marker in msg.content:
                        artifacts.append(f"[{subtopic}] {msg.content[:200]}")

        return {
            "branch_results": {subtopic: summary},
            "artifacts": artifacts,
            "progress": [f"Branch '{subtopic}' completed with summary ({len(summary)} chars)."],
        }

    async def synthesize(state: ParallelResearchState) -> dict:
        """Stage 3: barrier/join node — wait for all branches, then synthesize."""
        request = state.get("request") or ""
        branch_results = state.get("branch_results") or {}

        progress = (state.get("progress") or []) + [f"Stage 'synthesize' reached: joining {len(branch_results)} branch results."]

        report_sections = "\n\n".join(f"## {subtopic}\n{result}" for subtopic, result in branch_results.items())

        response = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "You are a technical writer. Synthesize the per-subtopic research "
                        "into a single coherent report. Resolve conflicts, unify themes, "
                        "and highlight the most important insights. Write in the same language "
                        "as the original request."
                    )
                ),
                HumanMessage(content=(f"Original request: {request}\n\nResearch by subtopic:\n\n{report_sections}")),
            ]
        )
        return {
            "messages": [response],
            "stage": "synthesis",
            "progress": progress + ["Stage 'synthesis' completed."],
        }

    graph.add_node("plan", plan)
    graph.add_node("research_branch", research_branch)
    graph.add_node("synthesize", synthesize)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_to_branches, ["research_branch"])
    graph.add_edge("research_branch", "synthesize")
    graph.add_edge("synthesize", END)

    return graph
