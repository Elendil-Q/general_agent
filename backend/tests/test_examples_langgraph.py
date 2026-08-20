"""Tests for the LangGraph workflow examples in ``deerflow.examples.langgraph``."""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.types import Command

from deerflow.examples.langgraph.cyber_ops import build_graph as build_cyber_ops_graph
from deerflow.examples.langgraph.cyber_ops.defensive import (
    build_graph as build_defensive_graph,
)
from deerflow.examples.langgraph.cyber_ops.graph import (
    _MAX_CLASSIFICATION_ATTEMPTS,
    CyberOpsState,
)
from deerflow.examples.langgraph.cyber_ops.offensive import (
    build_graph as build_offensive_graph,
)
from deerflow.examples.langgraph.cyber_ops.recon import (
    build_graph as build_recon_graph,
)
from deerflow.examples.langgraph.parallel_research import (
    ParallelResearchState,
)
from deerflow.examples.langgraph.parallel_research import (
    build_graph as build_parallel_research_graph,
)
from deerflow.examples.langgraph.research_approval import (
    ResearchApprovalState,
)
from deerflow.examples.langgraph.research_approval import (
    build_graph as build_research_approval_graph,
)


def _fake_model(responses=None):
    """Return a mock chat model whose ainvoke yields the provided responses."""
    if responses is None:
        responses = []
    model = MagicMock()
    state = {"idx": 0}

    async def _ainvoke(messages, **kwargs):
        response = responses[state["idx"] % len(responses)]
        state["idx"] += 1
        return response

    model.ainvoke = _ainvoke
    model.bind_tools = MagicMock(return_value=model)
    return model


class _CyberOpsConditionalModel:
    """Routes on system-prompt markers so the cyber_ops nodes are deterministic.

    The classify node's system prompt contains "classify the user request"; the
    re-classify variant additionally contains "previous classification" (seeded
    from the rejected human feedback). The three subgraphs use
    "offensive analyst" / "defensive analyst" / "recon analyst", and the
    summarize node uses "synthesiz".
    """

    def __init__(
        self,
        classify="offensive",
        after_feedback="defensive",
        offensive="offensive result",
        defensive="defensive result",
        recon="recon result",
        summarize="Final summary.",
    ):
        self.classify = classify
        self.after_feedback = after_feedback
        self.offensive = offensive
        self.defensive = defensive
        self.recon = recon
        self.summarize = summarize

    async def ainvoke(self, messages, **kwargs):
        text = " ".join(str(m.content) if isinstance(m.content, str) else "" for m in messages).lower()
        if "previous classification" in text:
            return AIMessage(content=self.after_feedback)
        if "classify the user request" in text:
            return AIMessage(content=self.classify)
        if "offensive analyst" in text:
            return AIMessage(content=self.offensive)
        if "defensive analyst" in text:
            return AIMessage(content=self.defensive)
        if "recon analyst" in text:
            return AIMessage(content=self.recon)
        if "synthesiz" in text:
            return AIMessage(content=self.summarize)
        return AIMessage(content="fallback")

    def bind_tools(self, tools, **kwargs):
        return self


class TestResearchApprovalExample:
    def test_build_graph_returns_uncompiled_state_graph(self):
        model = _fake_model()
        graph = build_research_approval_graph(model=model, tools=[], config=None)
        assert isinstance(graph, StateGraph)
        assert not isinstance(graph, CompiledStateGraph)

    @pytest.mark.anyio
    async def test_approved_path_completes_with_report(self):
        model = _fake_model(
            [
                # research node response
                AIMessage(content="Research notes: AI agents are trending."),
                # generate_report node response (terminal)
                AIMessage(content="Final report: AI agents are trending."),
            ]
        )
        graph = build_research_approval_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())

        initial_state: ResearchApprovalState = {
            "messages": [HumanMessage(content="Research AI agents")],
        }
        thread_id = "t1"

        # First run: research completes, then approve node hits interrupt().
        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
        assert result["stage"] == "research"
        assert "__interrupt__" in result

        # Resume with the structured single_choice answer (the selected option string).
        result = await graph.ainvoke(
            Command(resume="approve"),
            {"configurable": {"thread_id": thread_id}},
        )
        assert result["stage"] == "report"
        assert result["approved"] is True
        assert any("Stage 'report' completed" in p for p in result.get("progress", []))
        assert any(isinstance(m, AIMessage) and "Final report" in (m.content or "") for m in result.get("messages", []))

    @pytest.mark.anyio
    async def test_rejected_approval_routes_to_revise(self):
        model = _fake_model(
            [
                AIMessage(content="Research notes: AI agents are trending."),
                AIMessage(content="Revised report: AI agents are trending after feedback."),
            ]
        )
        graph = build_research_approval_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())

        initial_state: ResearchApprovalState = {
            "messages": [HumanMessage(content="Research AI agents")],
        }
        thread_id = "t2"

        # First run: research + interrupt at approve.
        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
        assert "__interrupt__" in result

        # Resume with the structured single_choice rejection answer.
        result = await graph.ainvoke(
            Command(resume="reject"),
            {"configurable": {"thread_id": thread_id}},
        )
        assert result["stage"] == "revise"
        assert result["approved"] is False
        assert any(isinstance(m, AIMessage) and "Revised report" in (m.content or "") for m in result.get("messages", []))


class TestParallelResearchExample:
    def test_build_graph_returns_uncompiled_state_graph(self):
        model = _fake_model()
        graph = build_parallel_research_graph(model=model, tools=[], config=None)
        assert isinstance(graph, StateGraph)
        assert not isinstance(graph, CompiledStateGraph)

    @pytest.mark.anyio
    async def test_plan_splits_subtopics(self):
        model = _fake_model(
            [
                AIMessage(content="healthcare, finance, education"),
            ]
        )
        graph = build_parallel_research_graph(model=model, tools=[], config=None).compile()
        initial_state: ParallelResearchState = {
            "messages": [HumanMessage(content="Research AI agents in multiple sectors")],
        }
        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": "t3"}})

        assert result.get("subtopics") == ["healthcare", "finance", "education"]
        assert result.get("request") == "Research AI agents in multiple sectors"
        assert any("Stage 'plan' completed" in p for p in result.get("progress", []))

    @pytest.mark.anyio
    async def test_end_to_end_synthesizes_report(self):
        responses = {
            "plan": AIMessage(content="healthcare, finance"),
            "healthcare": AIMessage(content="AI agents in healthcare improve triage."),
            "finance": AIMessage(content="AI agents in finance automate reporting."),
            "synthesize": AIMessage(content="Synthesis: AI agents transform healthcare and finance."),
        }

        class _ConditionalModel:
            async def ainvoke(self, messages, **kwargs):
                text = " ".join(str(m.content) if isinstance(m.content, str) else "" for m in messages).lower()
                if "planning assistant" in text:
                    return responses["plan"]
                if "research analyst" in text:
                    # The branch prompt quotes the subtopic, e.g. 'healthcare'.
                    if "'healthcare'" in text or "'healthcare'" in text.replace("'", "'"):
                        return responses["healthcare"]
                    return responses["finance"]
                if "technical writer" in text:
                    return responses["synthesize"]
                return responses["plan"]

            def bind_tools(self, tools, **kwargs):
                return self

        model = _ConditionalModel()
        graph = build_parallel_research_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())
        initial_state: ParallelResearchState = {
            "messages": [HumanMessage(content="AI agents in healthcare and finance")],
        }
        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": "t4"}})

        assert result["stage"] == "synthesis"
        branch_results = result.get("branch_results", {})
        assert "healthcare" in branch_results
        assert "finance" in branch_results
        assert any(isinstance(m, AIMessage) and "Synthesis" in (m.content or "") for m in result.get("messages", []))
        # Progress from parallel branches was aggregated.
        branch_progress = [p for p in result.get("progress", []) if "Branch '" in p]
        assert len(branch_progress) == 2


class TestToolNodeUsage:
    @pytest.mark.anyio
    async def test_research_node_forwards_full_state_to_tool_node(self):
        """ToolNode receives the full ThreadState so sandbox tools can resolve paths."""

        @tool
        def fake_tool(query: str) -> str:
            """A fake tool for tests."""
            return f"tool result for {query}"

        tool_response = AIMessage(
            content="",
            tool_calls=[{"id": "tc1", "name": "fake_tool", "args": {"query": "x"}}],
        )
        final_response = AIMessage(content="Research notes with tool result.")
        model = _fake_model([tool_response, final_response])

        graph = build_research_approval_graph(model=model, tools=[fake_tool], config=None).compile(checkpointer=InMemorySaver())
        initial_state: ResearchApprovalState = {
            "messages": [HumanMessage(content="Research AI agents")],
            "sandbox": {"sandbox_id": "local:thread-1"},
            "thread_data": {"workspace_path": "/tmp/ws"},
        }
        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": "t5"}})

        # The graph was interrupted at the approve node, but the research node ran
        # and the full ThreadState (including sandbox) was forwarded to ToolNode.
        assert result.get("sandbox", {}).get("sandbox_id") == "local:thread-1"
        # The tool call result was appended to messages.
        assert any(isinstance(m, ToolMessage) and "tool result" in (m.content or "") for m in result.get("messages", []))


class TestCyberOpsExample:
    def test_build_graph_returns_uncompiled_state_graph(self):
        model = _fake_model()
        graph = build_cyber_ops_graph(model=model, tools=[], config=None)
        assert isinstance(graph, StateGraph)
        assert not isinstance(graph, CompiledStateGraph)

    @pytest.mark.anyio
    async def test_approved_path_runs_selected_branch_and_summarizes(self):
        model = _CyberOpsConditionalModel(classify="offensive", summarize="Final report for the attack scenario.")
        graph = build_cyber_ops_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())

        initial_state: CyberOpsState = {"messages": [HumanMessage(content="Attack scenario")]}
        thread_id = "cyber-approve"

        # First run: classify completes, then confirm hits interrupt().
        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
        assert result["stage"] == "classify"
        assert "__interrupt__" in result
        assert result["selected_types"] == ["offensive"]

        # Resume with the structured single_choice approval.
        result = await graph.ainvoke(Command(resume="approve"), {"configurable": {"thread_id": thread_id}})
        assert result["stage"] == "summarize"
        assert result["confirmed"] is True
        assert result.get("branch_results") == {"offensive": "offensive result"}
        # Terminal AIMessage carries the summary.
        assert any(isinstance(m, AIMessage) and "Final report" in (m.content or "") for m in result.get("messages", []))
        # Intermediate progress is surfaced as AIMessages (the SSE task_running path).
        ai_contents = [m.content for m in result.get("messages", []) if isinstance(m, AIMessage)]
        assert any("任务分类结果" in (c or "") for c in ai_contents)
        assert any("[offensive] 分析完成" in (c or "") for c in ai_contents)

    @pytest.mark.anyio
    async def test_rejected_confirmation_reclassifies_with_feedback(self):
        model = _CyberOpsConditionalModel(
            classify="offensive",
            after_feedback="defensive",
            offensive="offensive result",
            defensive="defensive result",
            summarize="Final summary.",
        )
        graph = build_cyber_ops_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())

        initial_state: CyberOpsState = {"messages": [HumanMessage(content="Attack scenario")]}
        thread_id = "cyber-reject"

        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
        assert "__interrupt__" in result
        assert result["selected_types"] == ["offensive"]

        # Reject with free-form feedback (frontend "other" input path).
        result = await graph.ainvoke(
            Command(resume="This is defensive, not offensive."),
            {"configurable": {"thread_id": thread_id}},
        )
        # Reached confirm again: re-classified using the feedback.
        assert "__interrupt__" in result
        assert result["selected_types"] == ["defensive"]
        assert result["feedback"] == "This is defensive, not offensive."

        result = await graph.ainvoke(Command(resume="approve"), {"configurable": {"thread_id": thread_id}})
        assert result["stage"] == "summarize"
        assert result.get("branch_results") == {"defensive": "defensive result"}

    @pytest.mark.anyio
    async def test_multi_type_parallel_branches(self):
        model = _CyberOpsConditionalModel(
            classify="defensive, recon",
            defensive="defensive result",
            recon="recon result",
            summarize="Final summary.",
        )
        graph = build_cyber_ops_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())

        initial_state: CyberOpsState = {"messages": [HumanMessage(content="Dual scenario")]}
        thread_id = "cyber-parallel"

        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
        assert "__interrupt__" in result
        assert result["selected_types"] == ["defensive", "recon"]

        result = await graph.ainvoke(Command(resume="approve"), {"configurable": {"thread_id": thread_id}})
        assert result["stage"] == "summarize"
        assert result.get("branch_results") == {"defensive": "defensive result", "recon": "recon result"}
        # Both parallel branches appended progress.
        branch_progress = [p for p in result.get("progress", []) if "Branch '" in p]
        assert len(branch_progress) == 2

    @pytest.mark.anyio
    async def test_rejected_loop_exhausts_attempts_then_dispatch(self):
        model = _CyberOpsConditionalModel(
            classify="offensive",
            after_feedback="offensive",
            offensive="offensive result",
            summarize="Final summary.",
        )
        graph = build_cyber_ops_graph(model=model, tools=[], config=None).compile(checkpointer=InMemorySaver())

        initial_state: CyberOpsState = {"messages": [HumanMessage(content="Attack scenario")]}
        thread_id = "cyber-exhaust"

        result = await graph.ainvoke(initial_state, {"configurable": {"thread_id": thread_id}})
        assert "__interrupt__" in result

        # Keep rejecting; each rejection re-runs classify until attempts run out.
        for _ in range(_MAX_CLASSIFICATION_ATTEMPTS - 1):
            result = await graph.ainvoke(Command(resume="reject"), {"configurable": {"thread_id": thread_id}})
            assert "__interrupt__" in result

        # Attempts exhausted -> proceeds to dispatch despite the last rejection.
        result = await graph.ainvoke(Command(resume="reject"), {"configurable": {"thread_id": thread_id}})
        assert result["stage"] == "summarize"
        assert result["confirmed"] is False
        assert result.get("branch_results") == {"offensive": "offensive result"}
        assert any("达到最大重试次数" in (c or "") for c in result.get("progress", []))

    @pytest.mark.anyio
    async def test_subgraphs_run_standalone(self):
        standalone_cases = [
            (build_offensive_graph, "Attack scenario", "offensive conclusion"),
            (build_defensive_graph, "Defense scenario", "defensive conclusion"),
            (build_recon_graph, "Recon scenario", "recon conclusion"),
        ]
        for builder, request, expected in standalone_cases:
            model = _fake_model([AIMessage(content=expected)])
            graph = builder(model=model, tools=[], config=None).compile()
            result = await graph.ainvoke({"input": request, "messages": []})
            assert result.get("output") == expected
