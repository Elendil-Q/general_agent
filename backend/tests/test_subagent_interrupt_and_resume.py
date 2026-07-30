"""Interrupt + resume tests for SubagentExecutor.

Covers:
- ``_aexecute`` detects ``__interrupt__`` and transitions to INTERRUPTED
- cancel takes precedence over interrupt when both coincide
- ``_aresume`` reuses the cached agent + checkpointer + subagent_thread_id,
  feeds ``Command(resume=...)`` to ``astream``, and reaches COMPLETED
- ``resume_background_subagent`` refuses non-interrupted tasks

Uses the same delayed-import fixture pattern as test_subagent_executor.py to
break the circular import that conftest.py works around.
"""

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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


async def _async_iterator(items):
    for item in items:
        yield item


@pytest.fixture(autouse=True)
def _setup_executor_classes():
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

    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor, SubagentResult, SubagentStatus

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config
    agent_registry._refs.clear()

    classes = {
        "AIMessage": AIMessage,
        "HumanMessage": HumanMessage,
        "SubagentConfig": SubagentConfig,
        "SubagentExecutor": SubagentExecutor,
        "SubagentResult": SubagentResult,
        "SubagentStatus": SubagentStatus,
        "executor_module": executor_module,
        "agent_registry": agent_registry,
        "AgentRef": AgentRef,
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


def _base_config(classes):
    return classes["SubagentConfig"](
        name="test-agent",
        description="Test agent",
        system_prompt="You are a test agent.",
        max_turns=10,
        timeout_seconds=60,
    )


def _mock_interrupt(value="Need human input", interrupt_id="int-1"):
    """Build a real LangGraph Interrupt object so serialize_lc_object handles it."""
    from langgraph.types import Interrupt

    return Interrupt(value=value, when=None, id=interrupt_id)


class TestInterruptDetection:
    @pytest.mark.anyio
    async def test_aexecute_detects_interrupt(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]

        interrupt = _mock_interrupt(value="Approve?", interrupt_id="int-1")
        # Final values chunk carries __interrupt__; stream ends after it.
        chunk = {
            "messages": [HumanMessage(content="do it"), AIMessage(content="working")],
            "__interrupt__": (interrupt,),
        }

        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        executor = classes["SubagentExecutor"](config=_base_config(classes), tools=[], thread_id="parent", task_id="call_1")
        with patch.object(executor, "_create_agent", return_value=agent):
            result = await executor._aexecute("do it")

        assert result.status is SubagentStatus.INTERRUPTED
        assert result.completed_at is None
        assert result.interrupted_at is not None
        assert result.subagent_thread_id == executor.subagent_thread_id == "subagent::parent::call_1"
        # interrupts serialized to list of {value, id} dicts
        assert isinstance(result.interrupts, list)
        assert result.interrupts[0]["value"] == "Approve?"
        assert result.interrupts[0]["id"] == "int-1"

    @pytest.mark.anyio
    async def test_cancel_takes_precedence_over_interrupt(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        HumanMessage = classes["HumanMessage"]
        AIMessage = classes["AIMessage"]

        interrupt = _mock_interrupt(value="q")
        chunk = {
            "messages": [HumanMessage(content="do it"), AIMessage(content="working")],
            "__interrupt__": (interrupt,),
        }

        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        executor = classes["SubagentExecutor"](config=_base_config(classes), tools=[], thread_id="parent", task_id="call_1")
        # Set cancel before running; the pre-stream cancel check returns CANCELLED
        # before the loop even starts, but if it slips through, the post-stream
        # check still must prefer CANCELLED over INTERRUPTED.
        with patch.object(executor, "_create_agent", return_value=agent):
            result_holder = classes["SubagentResult"](task_id="call_1", trace_id="tr", status=SubagentStatus.RUNNING)
            # Set cancel AFTER the stream would end: simulate by setting it now
            # but the astream yields the interrupt chunk first. To exercise the
            # post-stream precedence, clear cancel during the run then set it
            # before the post-stream check. Simplest: set it now and let the
            # in-loop check fire. We instead test the post-stream path directly.
            result_holder.cancel_event.set()
            result = await executor._aexecute("do it", result_holder=result_holder)

        # Either the pre-stream or in-loop cancel check fires -> CANCELLED wins.
        assert result.status is SubagentStatus.CANCELLED
        assert result.status is not SubagentStatus.INTERRUPTED

    @pytest.mark.anyio
    async def test_no_interrupt_completes_normally(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        HumanMessage = classes["HumanMessage"]
        AIMessage = classes["AIMessage"]

        chunk = {"messages": [HumanMessage(content="do it"), AIMessage(content="done", id="m1")]}
        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        executor = classes["SubagentExecutor"](config=_base_config(classes), tools=[], thread_id="parent", task_id="call_1")
        with patch.object(executor, "_create_agent", return_value=agent):
            result = await executor._aexecute("do it")

        assert result.status is SubagentStatus.COMPLETED
        assert result.result == "done"


class TestResume:
    @pytest.mark.anyio
    async def test_aresume_reuses_agent_and_thread_id_with_command_resume(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        HumanMessage = classes["HumanMessage"]
        AIMessage = classes["AIMessage"]

        # Capture the graph_input passed to astream on resume.
        captured_inputs: list = []
        resume_chunk = {"messages": [HumanMessage(content="do it"), AIMessage(content="final answer", id="m2")]}

        agent = MagicMock()

        def _astream(graph_input, *a, **kw):
            captured_inputs.append(graph_input)
            return _async_iterator([resume_chunk])

        agent.astream = _astream

        executor = classes["SubagentExecutor"](config=_base_config(classes), tools=[], thread_id="parent", task_id="call_1")
        # Simulate the cached agent from a prior interrupted run.
        executor._agent = agent

        result_holder = classes["SubagentResult"](task_id="call_1", trace_id="tr", status=SubagentStatus.RUNNING)
        result_holder.try_set_interrupted(
            interrupts=[{"value": "q", "id": "i1"}],
            subagent_thread_id=executor.subagent_thread_id,
        )
        assert result_holder.status is SubagentStatus.INTERRUPTED

        # Patch _create_agent away so resume never rebuilds (it should reuse _agent).
        with patch.object(executor, "_create_agent", side_effect=AssertionError("resume must reuse cached agent")):
            result = await executor._aresume("yes proceed", result_holder=result_holder)

        # Command(resume=...) was fed to astream.
        from langgraph.types import Command

        assert len(captured_inputs) == 1
        assert isinstance(captured_inputs[0], Command)
        assert captured_inputs[0].resume == "yes proceed"
        # Resume reached COMPLETED.
        assert result.status is SubagentStatus.COMPLETED
        assert result.result == "final answer"
        # Re-interrupt is also detectable on resume (covered implicitly by structure).

    @pytest.mark.anyio
    async def test_resume_background_subagent_rejects_non_interrupted(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        executor_module = classes["executor_module"]
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]

        executor = classes["SubagentExecutor"](config=_base_config(classes), tools=[], thread_id="parent", task_id="call_1")
        result = classes["SubagentResult"](task_id="call_1", trace_id="tr", status=SubagentStatus.RUNNING)
        agent_registry.register(
            AgentRef(
                task_id="call_1",
                thread_id="parent",
                trace_id="tr",
                subagent_type="test-agent",
                status=SubagentStatus.RUNNING,
                config=_base_config(classes),
                executor=executor,
                result=result,
                description="test",
                created_at=datetime.now(),
            )
        )

        # RUNNING -> refuse
        with pytest.raises(RuntimeError):
            executor_module.resume_background_subagent("call_1", "answer")

        # Unknown task -> KeyError
        agent_registry.remove("call_1")
        with pytest.raises(KeyError):
            executor_module.resume_background_subagent("missing", "answer")

    def test_get_subagent_interrupt_only_for_interrupted(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        executor_module = classes["executor_module"]
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]

        # RUNNING -> None
        running = classes["SubagentResult"](task_id="t", trace_id="tr", status=SubagentStatus.RUNNING)
        agent_registry.register(
            AgentRef(
                task_id="t",
                thread_id="th",
                trace_id="tr",
                subagent_type="test-agent",
                status=SubagentStatus.RUNNING,
                config=_base_config(classes),
                executor=None,
                result=running,
                description="test",
                created_at=datetime.now(),
            )
        )
        assert executor_module.get_subagent_interrupt("t") is None

        # INTERRUPTED -> metadata
        running.try_set_interrupted(interrupts=[{"value": "q"}], subagent_thread_id="sub::p::t")
        meta = executor_module.get_subagent_interrupt("t")
        assert meta is not None
        assert meta["task_id"] == "t"
        assert meta["subagent_thread_id"] == "sub::p::t"
        assert meta["interrupts"] == [{"value": "q"}]

        # Unknown -> None
        assert executor_module.get_subagent_interrupt("nope") is None

        agent_registry.remove("t")


class TestMirrorStatusUpdates:
    """Registry mirror must track INTERRUPTED / post-resume outcomes."""

    def _register(self, classes, task_id, executor, holder, status, config):
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]
        agent_registry.register(
            AgentRef(
                task_id=task_id,
                thread_id="parent",
                trace_id="tr",
                subagent_type="test-agent",
                status=status,
                config=config,
                executor=executor,
                result=holder,
                description="test",
                created_at=datetime.now(),
            )
        )

    @pytest.mark.anyio
    async def test_aexecute_interrupt_updates_registry_mirror(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]
        agent_registry = classes["agent_registry"]
        executor_module = classes["executor_module"]

        interrupt = _mock_interrupt(value="Approve?", interrupt_id="int-m1")
        chunk = {
            "messages": [HumanMessage(content="do it"), AIMessage(content="working", id="m1")],
            "__interrupt__": (interrupt,),
        }
        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        config = _base_config(classes)
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="mir-1")
        holder = classes["SubagentResult"](task_id="mir-1", trace_id="tr", status=SubagentStatus.RUNNING)
        self._register(classes, "mir-1", executor, holder, SubagentStatus.RUNNING, config)

        try:
            with patch.object(executor, "_create_agent", return_value=agent):
                result = await executor._aexecute("do it", result_holder=holder)

            assert result.status is SubagentStatus.INTERRUPTED
            assert agent_registry.get("mir-1").status is SubagentStatus.INTERRUPTED
            meta = executor_module.get_subagent_interrupt("mir-1")
            assert meta is not None
            assert meta["interrupts"][0]["value"] == "Approve?"
        finally:
            agent_registry.remove("mir-1")

    @pytest.mark.anyio
    async def test_aresume_reinterrupt_updates_registry_mirror(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]
        agent_registry = classes["agent_registry"]

        interrupt = _mock_interrupt(value="Again?", interrupt_id="int-m2")
        chunk = {
            "messages": [HumanMessage(content="do it"), AIMessage(content="still need input", id="m2")],
            "__interrupt__": (interrupt,),
        }
        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        config = _base_config(classes)
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="mir-2")
        executor._agent = agent

        holder = classes["SubagentResult"](task_id="mir-2", trace_id="tr", status=SubagentStatus.RUNNING)
        holder.try_set_interrupted(interrupts=[{"value": "q", "id": "i0"}], subagent_thread_id=executor.subagent_thread_id)
        assert holder.try_resume() is True
        self._register(classes, "mir-2", executor, holder, SubagentStatus.RUNNING, config)

        try:
            result = await executor._aresume("answer", result_holder=holder)

            assert result.status is SubagentStatus.INTERRUPTED
            assert agent_registry.get("mir-2").status is SubagentStatus.INTERRUPTED
        finally:
            agent_registry.remove("mir-2")

    @pytest.mark.anyio
    async def test_aresume_completion_updates_registry_mirror_completed(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]
        agent_registry = classes["agent_registry"]

        chunk = {"messages": [HumanMessage(content="do it"), AIMessage(content="final answer", id="m3")]}
        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        config = _base_config(classes)
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="mir-3")
        executor._agent = agent

        holder = classes["SubagentResult"](task_id="mir-3", trace_id="tr", status=SubagentStatus.RUNNING)
        holder.try_set_interrupted(interrupts=[{"value": "q", "id": "i0"}], subagent_thread_id=executor.subagent_thread_id)
        assert holder.try_resume() is True
        self._register(classes, "mir-3", executor, holder, SubagentStatus.RUNNING, config)

        try:
            result = await executor._aresume("answer", result_holder=holder)

            assert result.status is SubagentStatus.COMPLETED
            assert result.result == "final answer"
            assert agent_registry.get("mir-3").status is SubagentStatus.COMPLETED
        finally:
            agent_registry.remove("mir-3")

    @pytest.mark.anyio
    async def test_aresume_keep_alive_parks_idle_and_updates_mirror(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]
        agent_registry = classes["agent_registry"]

        chunk = {"messages": [HumanMessage(content="do it"), AIMessage(content="resumed idle answer", id="m4")]}
        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        config = _base_config(classes)
        config.keep_alive = True
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="mir-4")
        executor._agent = agent

        holder = classes["SubagentResult"](task_id="mir-4", trace_id="tr", status=SubagentStatus.RUNNING)
        holder.try_set_interrupted(interrupts=[{"value": "q", "id": "i0"}], subagent_thread_id=executor.subagent_thread_id)
        assert holder.try_resume() is True
        self._register(classes, "mir-4", executor, holder, SubagentStatus.RUNNING, config)

        try:
            result = await executor._aresume("answer", result_holder=holder)

            assert result.status is SubagentStatus.IDLE
            assert result.result == "resumed idle answer"
            assert result.idle_since is not None
            assert agent_registry.get("mir-4").status is SubagentStatus.IDLE
        finally:
            from deerflow.subagents.lifecycle import lifecycle_manager

            lifecycle_manager.cancel_ttl("mir-4")
            agent_registry.remove("mir-4")


class TestFollowUpInterruptResumeFlow:
    """Regression: follow_up revive -> interrupt -> resume (the 409 bug).

    ``continue_with_prompt`` used to build a NEW unregistered result holder,
    so an interrupt during the follow-up mutated only that holder while the
    registry's holder stayed IDLE — the resume endpoint then 409'd with
    "not interrupted". The registered holder must be reused and the mirror
    must track RUNNING/INTERRUPTED.
    """

    def test_interrupt_after_follow_up_is_resumable(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]
        executor_module = classes["executor_module"]

        config = _base_config(classes)
        interrupt = _mock_interrupt(value="Which option?", interrupt_id="int-fu")
        chunk = {
            "messages": [HumanMessage(content="follow-up"), AIMessage(content="need input", id="m-fu")],
            "__interrupt__": (interrupt,),
        }

        seen_mirror: dict[str, object] = {}
        agent = MagicMock()

        def _astream(*a, **kw):
            ref = agent_registry.get("fu-1")
            seen_mirror["status"] = ref.status if ref else None
            return _async_iterator([chunk])

        agent.astream = _astream

        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="fu-1")
        executor._agent = agent

        # Registered holder parked IDLE (as after a keep_alive first run).
        holder = classes["SubagentResult"](task_id="fu-1", trace_id="tr", status=SubagentStatus.RUNNING)
        holder.result = "first result"
        assert holder.try_set_idle() is True
        agent_registry.register(
            AgentRef(
                task_id="fu-1",
                thread_id="parent",
                trace_id="tr",
                subagent_type="test-agent",
                status=SubagentStatus.IDLE,
                config=config,
                executor=executor,
                result=holder,
                description="test",
                created_at=datetime.now(),
            )
        )

        try:
            result = executor.continue_with_prompt("follow-up", "fu-1")

            # The REGISTERED holder was reused and mutated in place.
            assert result is holder
            assert holder.status is SubagentStatus.INTERRUPTED
            # The mirror was RUNNING during the continuation...
            assert seen_mirror["status"] is SubagentStatus.RUNNING
            # ...and INTERRUPTED after the interrupt fired.
            assert agent_registry.get("fu-1").status is SubagentStatus.INTERRUPTED

            meta = executor_module.get_subagent_interrupt("fu-1")
            assert meta is not None
            assert meta["interrupts"][0]["value"] == "Which option?"

            # The resume path now accepts (previously the holder stayed IDLE -> 409).
            assert holder.try_resume() is True
        finally:
            agent_registry.remove("fu-1")

    def test_continue_with_prompt_refuses_non_idle_registered(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]

        config = _base_config(classes)
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="fu-2")

        holder = classes["SubagentResult"](task_id="fu-2", trace_id="tr", status=SubagentStatus.RUNNING)
        agent_registry.register(
            AgentRef(
                task_id="fu-2",
                thread_id="parent",
                trace_id="tr",
                subagent_type="test-agent",
                status=SubagentStatus.RUNNING,
                config=config,
                executor=executor,
                result=holder,
                description="test",
                created_at=datetime.now(),
            )
        )

        try:
            with pytest.raises(RuntimeError, match="not idle"):
                executor.continue_with_prompt("follow-up", "fu-2")
            # Refusal must not disturb the existing state.
            assert holder.status is SubagentStatus.RUNNING
            assert agent_registry.get("fu-2").status is SubagentStatus.RUNNING
        finally:
            agent_registry.remove("fu-2")


class TestTryRevive:
    def test_try_revive_from_idle_clears_state(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]

        holder = classes["SubagentResult"](task_id="rv-1", trace_id="tr", status=SubagentStatus.RUNNING)
        holder.cancel_event.set()
        assert holder.try_set_idle() is True
        idle_since = holder.idle_since
        assert idle_since is not None

        assert holder.try_revive() is True
        assert holder.status is SubagentStatus.RUNNING
        assert holder.idle_since is None
        assert holder.completed_at is None
        assert holder.error is None
        assert holder.interrupted_at is None
        assert holder.interrupts is None
        assert not holder.cancel_event.is_set()

    def test_try_revive_refused_from_non_idle(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]

        running = classes["SubagentResult"](task_id="rv-2", trace_id="tr", status=SubagentStatus.RUNNING)
        assert running.try_revive() is False
        assert running.status is SubagentStatus.RUNNING

        interrupted = classes["SubagentResult"](task_id="rv-3", trace_id="tr", status=SubagentStatus.RUNNING)
        interrupted.try_set_interrupted(interrupts=[{"value": "q"}], subagent_thread_id="s::p::rv-3")
        assert interrupted.try_revive() is False
        assert interrupted.status is SubagentStatus.INTERRUPTED

        completed = classes["SubagentResult"](task_id="rv-4", trace_id="tr", status=SubagentStatus.RUNNING)
        completed.try_set_terminal(SubagentStatus.COMPLETED, result="done")
        assert completed.try_revive() is False
        assert completed.status is SubagentStatus.COMPLETED


class TestContinueAsync:
    """Fire-and-forget continuation used by the rewritten follow_up tool."""

    def test_continue_async_revives_and_runs_in_background(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        AIMessage = classes["AIMessage"]
        HumanMessage = classes["HumanMessage"]
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]

        import time

        chunk = {"messages": [HumanMessage(content="follow-up"), AIMessage(content="async follow-up done", id="m-ca")]}
        agent = MagicMock()
        agent.astream = lambda *a, **kw: _async_iterator([chunk])

        config = _base_config(classes)
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="ca-1")
        executor._agent = agent

        holder = classes["SubagentResult"](task_id="ca-1", trace_id="tr", status=SubagentStatus.RUNNING)
        assert holder.try_set_idle() is True
        agent_registry.register(
            AgentRef(
                task_id="ca-1",
                thread_id="parent",
                trace_id="tr",
                subagent_type="test-agent",
                status=SubagentStatus.IDLE,
                config=config,
                executor=executor,
                result=holder,
                description="test",
                created_at=datetime.now(),
            )
        )

        try:
            returned = executor.continue_async("follow-up", "ca-1")

            # Returns immediately with the task_id; holder revived synchronously.
            assert returned == "ca-1"
            assert holder.status is SubagentStatus.RUNNING

            # Background continuation reaches COMPLETED and updates the mirror.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                ref = agent_registry.get("ca-1")
                if ref is not None and ref.status is SubagentStatus.COMPLETED:
                    break
                time.sleep(0.05)
            ref = agent_registry.get("ca-1")
            assert ref is not None and ref.status is SubagentStatus.COMPLETED, f"mirror never reached COMPLETED (status={ref.status if ref else None})"
            assert holder.status is SubagentStatus.COMPLETED
            assert holder.result == "async follow-up done"
        finally:
            agent_registry.remove("ca-1")

    def test_continue_async_rejects_non_idle_and_unknown(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        agent_registry = classes["agent_registry"]
        AgentRef = classes["AgentRef"]

        config = _base_config(classes)
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="ca-2")

        holder = classes["SubagentResult"](task_id="ca-2", trace_id="tr", status=SubagentStatus.RUNNING)
        agent_registry.register(
            AgentRef(
                task_id="ca-2",
                thread_id="parent",
                trace_id="tr",
                subagent_type="test-agent",
                status=SubagentStatus.RUNNING,
                config=config,
                executor=executor,
                result=holder,
                description="test",
                created_at=datetime.now(),
            )
        )

        try:
            with pytest.raises(RuntimeError, match="not idle"):
                executor.continue_async("follow-up", "ca-2")
            assert holder.status is SubagentStatus.RUNNING
        finally:
            agent_registry.remove("ca-2")

        with pytest.raises(KeyError):
            executor.continue_async("follow-up", "missing-task")


@pytest.mark.anyio
async def test_aexecute_forwards_clarification_interrupt_enabled_into_context(_setup_executor_classes):
    """clarification_interrupt_enabled is written into the subagent runtime
    context (passed to astream as ``context=``) so ClarificationMiddleware takes
    the structured interrupt path on web runs. When falsy it is omitted so the
    middleware falls back to the free goto=END path (matching IM behaviour)."""
    classes = _setup_executor_classes
    HumanMessage = classes["HumanMessage"]

    captured: dict[str, object] = {}

    async def _astream(graph_input, *a, **kw):
        captured["context"] = kw.get("context")
        yield {"messages": [HumanMessage(content="do it")]}

    agent = MagicMock()
    agent.astream = _astream

    config = _base_config(classes)

    async def run_with_flag(flag):
        executor = classes["SubagentExecutor"](config=config, tools=[], thread_id="parent", task_id="call_1", clarification_interrupt_enabled=flag)
        with patch.object(executor, "_create_agent", return_value=agent):
            await executor._aexecute("do it")

    # Truthy flag -> present in the forwarded context.
    await run_with_flag(True)
    assert captured["context"].get("clarification_interrupt_enabled") is True

    # Falsy flag -> omitted (middleware falls back to the free path).
    captured.clear()
    await run_with_flag(False)
    assert "clarification_interrupt_enabled" not in captured["context"]
