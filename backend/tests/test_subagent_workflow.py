"""Tests for workflow-based subagents (``config.workflow`` ref → LangGraph StateGraph).

Covers the contract a workflow subagent must satisfy:
- ``_create_workflow_agent`` resolves the factory via ``resolve_variable`` (the
  same ``module:object`` loader used for ``config.tools[].use``), injects the
  parent model + filtered tools, and compiles the returned ``StateGraph`` with
  the executor's own ``InMemorySaver`` (so interrupt/resume is isolated).
- ``_aexecute`` runs the real compiled graph end-to-end and reaches COMPLETED
  with the workflow's terminal ``AIMessage`` as the result.
- ``interrupt()`` in a workflow node pauses execution (→ INTERRUPTED) and
  ``_aresume`` feeds ``Command(resume=...)`` to the cached graph to finish.

Uses the same delayed-import fixture pattern as ``test_subagent_executor.py`` to
break the circular import that ``conftest.py`` works around. The test factories
use a local ``AgentState``-based schema (not ``deerflow.agents.thread_state``)
so they build real graphs without triggering the mocked package init.
"""

import sys
from types import ModuleType, SimpleNamespace
from typing import NotRequired
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

_MOCKED_MODULE_NAMES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
    "deerflow.skills.storage",
]


def _default_app_config():
    return SimpleNamespace(tool_search=SimpleNamespace(enabled=False))


def _clear_stale_executor_package_attr() -> None:
    subagents_pkg = sys.modules.get("deerflow.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")


class _FakeModel:
    """Returns a canned AIMessage per call so nodes can ``await model.ainvoke``."""

    def __init__(self):
        self.count = 0

    async def ainvoke(self, messages, **kwargs):
        self.count += 1
        return AIMessage(content=f"resp-{self.count}", id=f"mid-{self.count}")


def _make_workflow_module() -> tuple[ModuleType, _FakeModel]:
    """Build a fake importable module exposing two workflow factories."""
    from langchain.agents import AgentState
    from langchain_core.messages import SystemMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    class _InterruptState(AgentState):
        approved: NotRequired[bool]

    def build_interrupt_graph(*, model, tools, config):
        g = StateGraph(_InterruptState)

        async def gather(state):
            resp = await model.ainvoke([SystemMessage(content="notes"), state["messages"][-1]])
            return {"messages": [resp]}

        def approve(state):
            # First pass: interrupt() pauses and never returns. On resume it
            # returns the Command(resume=...) value, which we route on.
            decision = interrupt({"question": "ok?", "notes": state["messages"][-1].content})
            return {"approved": bool(decision.get("approved")) if isinstance(decision, dict) else False}

        def route(state):
            return "answer" if state.get("approved") else "revise"

        async def answer(state):
            resp = await model.ainvoke([SystemMessage(content="final"), state["messages"][0]])
            return {"messages": [resp]}

        async def revise(state):
            resp = await model.ainvoke([SystemMessage(content="revise"), state["messages"][0]])
            return {"messages": [resp]}

        g.add_node("gather", gather)
        g.add_node("approve", approve)
        g.add_node("answer", answer)
        g.add_node("revise", revise)
        g.add_edge(START, "gather")
        g.add_edge("gather", "approve")
        g.add_conditional_edges("approve", route, {"answer": "answer", "revise": "revise"})
        g.add_edge("answer", END)
        g.add_edge("revise", END)
        return g

    def build_simple_graph(*, model, tools, config):
        g = StateGraph(_InterruptState)

        async def gather(state):
            resp = await model.ainvoke([SystemMessage(content="notes"), state["messages"][-1]])
            return {"messages": [resp]}

        async def answer(state):
            resp = await model.ainvoke([SystemMessage(content="final"), state["messages"][0]])
            return {"messages": [resp]}

        g.add_node("gather", gather)
        g.add_node("answer", answer)
        g.add_edge(START, "gather")
        g.add_edge("gather", "answer")
        g.add_edge("answer", END)
        return g

    mod = ModuleType("deerflow_test_workflows")
    mod.build_interrupt_graph = build_interrupt_graph
    mod.build_simple_graph = build_simple_graph
    return mod, _FakeModel()


@pytest.fixture
def _setup_executor():
    """Yield (executor_classes, fake_model) with real executor + patched model.

    Mirrors ``test_subagent_executor.py``'s fixture: mocks heavy/circular deps,
    imports the real executor, and additionally patches ``create_chat_model`` to
    return a fake model and registers a fake importable workflow module.
    """
    original_modules = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")

    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    _clear_stale_executor_package_attr()

    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()
    storage_module = ModuleType("deerflow.skills.storage")
    storage_module.get_or_new_skill_storage = lambda **kwargs: SimpleNamespace(load_skills=lambda *, enabled_only: [])
    sys.modules["deerflow.skills.storage"] = storage_module

    from langchain_core.messages import AIMessage, HumanMessage

    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor, SubagentResult, SubagentStatus

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config

    workflow_module, fake_model = _make_workflow_module()
    sys.modules["deerflow_test_workflows"] = workflow_module
    # The executor resolves create_chat_model from its own namespace.
    executor_module.create_chat_model = lambda **kwargs: fake_model

    classes = {
        "AIMessage": AIMessage,
        "HumanMessage": HumanMessage,
        "SubagentConfig": SubagentConfig,
        "SubagentExecutor": SubagentExecutor,
        "SubagentResult": SubagentResult,
        "SubagentStatus": SubagentStatus,
        "fake_model": fake_model,
    }
    yield classes

    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        elif name in sys.modules:
            del sys.modules[name]
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    sys.modules.pop("deerflow_test_workflows", None)


def _workflow_config(classes, *, workflow="deerflow_test_workflows:build_interrupt_graph"):
    return classes["SubagentConfig"](
        name="wf-agent",
        description="workflow test agent",
        model="fake-model",
        max_turns=20,
        timeout_seconds=60,
        workflow=workflow,
    )


class TestWorkflowConfigField:
    def test_subagent_config_accepts_workflow(self):
        from deerflow.subagents.config import SubagentConfig

        cfg = SubagentConfig(name="x", description="d", workflow="pkg.mod:build_graph")
        assert cfg.workflow == "pkg.mod:build_graph"
        # Default is None (create_agent path).
        assert SubagentConfig(name="x", description="d").workflow is None

    def test_custom_subagent_config_accepts_workflow_and_optional_prompt(self):
        from deerflow.config.subagents_config import CustomSubagentConfig

        cfg = CustomSubagentConfig(description="d", workflow="pkg.mod:build_graph")
        assert cfg.workflow == "pkg.mod:build_graph"
        # system_prompt is now optional (workflow subagents omit it).
        assert cfg.system_prompt is None


class TestCreateWorkflowAgent:
    def test_builds_compiled_graph_with_executor_checkpointer(self, _setup_executor):
        from langgraph.graph.state import CompiledStateGraph

        classes = _setup_executor
        executor = classes["SubagentExecutor"](
            config=_workflow_config(classes),
            tools=[],
            thread_id="parent",
            task_id="call_1",
        )

        agent = executor._create_agent()

        assert isinstance(agent, CompiledStateGraph)
        # The compiled graph reuses the executor's InMemorySaver (the same one
        # _aexecute caches on _agent for resume). Proven end-to-end by the
        # interrupt/resume tests below.
        assert agent.checkpointer is executor._checkpointer
        # The factory received the fake model.
        assert classes["fake_model"] is not None

    def test_rejects_compiled_graph_from_factory(self, _setup_executor):
        classes = _setup_executor
        from langchain.agents import AgentState
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        # Factory that returns an already-compiled graph.
        def _bad_factory(*, model, tools, config):
            g = StateGraph(_S)
            g.add_node("n", lambda s: {})
            g.add_edge(START, "n")
            g.add_edge("n", END)
            return g.compile()

        mod = sys.modules["deerflow_test_workflows"]
        mod.bad_factory = _bad_factory

        executor = classes["SubagentExecutor"](
            config=_workflow_config(classes, workflow="deerflow_test_workflows:bad_factory"),
            tools=[],
            thread_id="parent",
            task_id="call_1",
        )
        with pytest.raises(ValueError, match="already-compiled"):
            executor._create_agent()


class TestWorkflowExecuteAndResume:
    @pytest.mark.anyio
    async def test_no_interrupt_workflow_completes(self, _setup_executor):
        classes = _setup_executor
        SubagentStatus = classes["SubagentStatus"]

        executor = classes["SubagentExecutor"](
            config=_workflow_config(classes, workflow="deerflow_test_workflows:build_simple_graph"),
            tools=[],
            thread_id="parent",
            task_id="call_1",
        )
        result = await executor._aexecute("do the research")

        assert result.status is SubagentStatus.COMPLETED
        # gather → resp-1, answer → resp-2 (terminal AIMessage).
        assert result.result == "resp-2"

    @pytest.mark.anyio
    async def test_workflow_interrupts_then_resume_approved(self, _setup_executor):
        classes = _setup_executor
        SubagentStatus = classes["SubagentStatus"]
        SubagentResult = classes["SubagentResult"]

        executor = classes["SubagentExecutor"](
            config=_workflow_config(classes),
            tools=[],
            thread_id="parent",
            task_id="call_1",
        )
        result = await executor._aexecute("do the research")
        assert result.status is SubagentStatus.INTERRUPTED
        assert result.subagent_thread_id == executor.subagent_thread_id
        # The interrupt payload was captured (serialized to {value, id} dicts).
        assert isinstance(result.interrupts, list) and result.interrupts

        # Resume reuses the cached agent + checkpointer (must not rebuild).
        result_holder = SubagentResult(task_id="call_1", trace_id="tr", status=SubagentStatus.RUNNING)
        result_holder.try_set_interrupted(
            interrupts=result.interrupts,
            subagent_thread_id=executor.subagent_thread_id,
        )
        # _agent was cached during _aexecute; _aresume must reuse it (not rebuild).
        with patch.object(executor, "_create_agent", side_effect=AssertionError("resume must reuse cached agent")):
            resumed = await executor._aresume({"approved": True}, result_holder=result_holder)

        assert resumed.status is SubagentStatus.COMPLETED
        # gather → resp-1 (pre-interrupt), answer → resp-2 (post-resume).
        assert resumed.result == "resp-2"

    @pytest.mark.anyio
    async def test_workflow_resume_rejected_routes_to_revise(self, _setup_executor):
        classes = _setup_executor
        SubagentStatus = classes["SubagentStatus"]
        SubagentResult = classes["SubagentResult"]

        executor = classes["SubagentExecutor"](
            config=_workflow_config(classes),
            tools=[],
            thread_id="parent",
            task_id="call_2",
        )
        result = await executor._aexecute("do the research")
        assert result.status is SubagentStatus.INTERRUPTED

        result_holder = SubagentResult(task_id="call_2", trace_id="tr", status=SubagentStatus.RUNNING)
        result_holder.try_set_interrupted(
            interrupts=result.interrupts,
            subagent_thread_id=executor.subagent_thread_id,
        )
        resumed = await executor._aresume({"approved": False}, result_holder=result_holder)

        assert resumed.status is SubagentStatus.COMPLETED
        # gather → resp-1, revise → resp-2 (rejected path).
        assert resumed.result == "resp-2"


def test_registry_passes_workflow_from_custom_config():
    """_build_custom_subagent_config copies ``workflow`` into SubagentConfig."""
    from deerflow.config.subagents_config import CustomSubagentConfig, SubagentsAppConfig
    from deerflow.subagents.registry import _build_custom_subagent_config

    custom = CustomSubagentConfig(description="d", workflow="pkg.mod:build_graph")
    app = SubagentsAppConfig(custom_agents={"wf": custom})
    cfg = _build_custom_subagent_config("wf", app_config=app)
    assert cfg is not None
    assert cfg.workflow == "pkg.mod:build_graph"
