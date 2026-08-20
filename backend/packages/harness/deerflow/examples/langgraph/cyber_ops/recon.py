"""Reconnaissance-analysis subgraph for the ``cyber_ops`` workflow.

A single-LLM-node ``StateGraph`` that takes the user request as ``input`` and
returns a reconnaissance-analysis conclusion as ``output``. Kept deliberately
minimal so the prompt can be extended per the real use case.
"""

from typing import NotRequired

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from deerflow.agents.thread_state import ThreadState

# TODO: 按实际业务补充侦察分析提示词。
SYSTEM_PROMPT = "You are a recon analyst. Based on the user's request, produce a concise reconnaissance-analysis conclusion."


class ReconState(ThreadState):
    """State schema for the recon subgraph (extends the executor ThreadState)."""

    # Original user request (seeded by the orchestrator).
    input: NotRequired[str | None]
    # Analysis conclusion produced by the LLM node.
    output: NotRequired[str | None]


def build_graph(*, model, tools, config) -> StateGraph:  # type: ignore[override]
    """Build the reconnaissance-analysis subgraph (uncompiled).

    Args:
        model: The chat model resolved by the executor.
        tools: Policy-filtered tools (ignored; the subgraph is model-only).
        config: ``SubagentConfig`` for the parent workflow.
    """

    async def analyze(state: ReconState) -> dict:
        response = await model.ainvoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=state.get("input") or ""),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return {"output": content}

    graph = StateGraph(ReconState)
    graph.add_node("analyze", analyze)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", END)
    return graph
