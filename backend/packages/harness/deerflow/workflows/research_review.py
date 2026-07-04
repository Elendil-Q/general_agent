"""Research → Approve → Answer workflow subagent.

A strict, deterministic node/edge flow (not a single model↔tools loop) for
scenarios that need a fixed pipeline with a mandatory human-checkpoint — while
still letting the model call file/sandbox tools inside the research step.
Demonstrates the workflow-subagent contract:

* factory signature ``build_graph(*, model, tools, config) -> StateGraph``;
* a ``ThreadState``-extended schema (adds an ``approved`` flag);
* calling file/sandbox tools via a ``ToolNode`` — the ``ToolRuntime`` is
  injected from the ``context``/``state`` the executor passes to ``astream``,
  so ``read_file``/``write_file``/``bash``/... work the same as in a
  ``create_agent`` subagent (sandbox isolation, virtual paths, audit-free
  since the shared middleware chain does not apply to workflows);
* HITL via ``interrupt()`` in the ``approve`` node (resumed by
  ``POST /api/threads/{tid}/subagents/{task_id}/resume`` with ``{"resume": ...}``);
* a terminal node that emits the final ``AIMessage`` consumed by
  ``SubagentExecutor._extract_final_result``.

Register it in ``config.yaml``::

    subagents:
      custom_agents:
        research-review:
          description: "Research a topic (with file tools), get approval, then answer"
          workflow: "deerflow.workflows.research_review:build_graph"
          max_turns: 40
          timeout_seconds: 1200

Note: the shared subagent middleware chain (sandbox lifecycle, guardrails,
audit) does NOT auto-apply to workflow subagents — the graph owns its flow.
Sandbox/MCP tools still work when routed through a ``ToolNode`` (the
``ToolRuntime`` is injected from the ``context`` the executor passes to
``astream``); ``interrupt()`` is available natively for HITL.
"""

from typing import NotRequired

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from deerflow.agents.thread_state import ThreadState


class ResearchReviewState(ThreadState):
    """Workflow state. Extends ThreadState (messages/sandbox/thread_data/...) so
    the executor's injected initial state is compatible, and adds an ``approved``
    flag the approve/router nodes use."""

    approved: NotRequired[bool]


# Bounded number of model↔tools rounds inside the investigate step, so a
# tool-happy model cannot loop forever. The executor's own ``recursion_limit``
# (``config.max_turns``) is a separate, outer cap on the whole graph.
_MAX_TOOL_ROUNDS = 8


def build_graph(*, model, tools, config) -> StateGraph:  # type: ignore[override]
    """Build the research-review workflow graph (uncompiled).

    Args:
        model: The resolved chat model (parent model, thinking disabled, no
            tracing).
        tools: Policy-filtered tools available to the workflow. When non-empty
            the model is bound to them and may call file/sandbox tools (e.g.
            ``read_file``/``write_file``/``ls``/``bash``) during research,
            executed through a ``ToolNode`` with full ``ToolRuntime`` injection.
            Pass ``[]``/``None`` for a model-only flow.
        config: The ``SubagentConfig`` (name, max_turns, ...).
    """
    graph = StateGraph(ResearchReviewState)

    tool_list = list(tools or [])
    bound_model = model.bind_tools(tool_list) if tool_list else model
    tool_node = ToolNode(tool_list) if tool_list else None

    async def investigate(state: ResearchReviewState) -> dict:
        """Step 1: research the request, optionally calling file/sandbox tools.

        A bounded model↔tools sub-loop: the model may emit ``tool_calls`` (e.g.
        ``read_file`` to inspect workspace files), which ``ToolNode`` executes
        with the injected ``ToolRuntime`` (sandbox + thread_data from state,
        thread_id/app_config from context). The loop ends when the model
        produces a tool-free message — the research summary.

        ``ToolNode`` reads ``runtime.state`` from the dict passed to it, so we
        forward the full graph state (``sandbox``/``thread_data``) alongside
        the working message list — otherwise the sandbox tools would see an
        empty workspace and refuse to read/write.
        """
        messages: list = [
            SystemMessage(content="You are a research analyst. Use the available file/sandbox tools to inspect the workspace as needed, then produce concise factual research notes. No final answer yet."),
            state["messages"][-1],
        ]
        new_messages: list = []
        for _ in range(_MAX_TOOL_ROUNDS):
            response = await bound_model.ainvoke(messages)
            new_messages.append(response)
            messages.append(response)
            if not getattr(response, "tool_calls", None):
                break
            # Forward the full state so ToolRuntime.state carries sandbox_id +
            # thread_data (workspace/uploads/outputs paths) the tools need.
            tool_result = await tool_node.ainvoke({**state, "messages": messages})
            tool_messages = tool_result["messages"]
            new_messages.extend(tool_messages)
            messages.extend(tool_messages)
        return {"messages": new_messages}

    def approve(state: ResearchReviewState) -> dict:
        """Step 2: pause for human approval of the research notes.

        ``interrupt()`` suspends the graph; the resume value is returned here.
        Resume with ``{"approved": true}`` to proceed, or
        ``{"approved": false, "feedback": "..."}`` to revise.
        """
        notes = state["messages"][-1].content if state.get("messages") else ""
        decision = interrupt({"question": "Approve these research notes before answering?", "notes": notes})
        if isinstance(decision, dict) and decision.get("approved"):
            return {"approved": True}
        return {"approved": False}

    def route_after_approve(state: ResearchReviewState) -> str:
        return "answer" if state.get("approved") else "revise"

    async def answer(state: ResearchReviewState) -> dict:
        """Step 3a (approved): produce the final answer from the notes."""
        task = state["messages"][0]
        notes = state["messages"][-1].content if state.get("messages") else ""
        response = await model.ainvoke(
            [
                SystemMessage(content="Using the approved research notes, write the final answer for the user."),
                HumanMessage(content=f"Request: {task.content}\n\nNotes:\n{notes}"),
            ]
        )
        # Terminal AIMessage — consumed by _extract_final_result.
        return {"messages": [response]}

    async def revise(state: ResearchReviewState) -> dict:
        """Step 3b (rejected): re-research briefly and answer, accounting for rejection."""
        task = state["messages"][0]
        response = await model.ainvoke(
            [
                SystemMessage(content="The reviewer rejected the prior notes. Re-research concisely and write the final answer."),
                HumanMessage(content=task.content if isinstance(task.content, str) else str(task.content)),
            ]
        )
        return {"messages": [response]}

    graph.add_node("investigate", investigate)
    graph.add_node("approve", approve)
    graph.add_node("answer", answer)
    graph.add_node("revise", revise)
    graph.add_edge(START, "investigate")
    graph.add_edge("investigate", "approve")
    graph.add_conditional_edges("approve", route_after_approve, {"answer": "answer", "revise": "revise"})
    graph.add_edge("answer", END)
    graph.add_edge("revise", END)
    return graph
