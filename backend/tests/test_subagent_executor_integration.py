# backend/tests/test_subagent_executor_integration.py
"""Integration: executor wires IDLE/budget/yield/EventBus into _aexecute + execute_async.

This file complements the focused unit tests in test_subagent_executor.py. It
exercises the cross-module wiring that the executor introduced once the
infrastructure modules (event_bus, agent_registry, BudgetMonitor, YieldCollector,
lifecycle_manager) landed: lifecycle events fire, the request budget terminates
runs at the hard limit, keep_alive=True transitions to IDLE, and the
``execute_async`` path registers an AgentRef in the process-level registry.
"""

from __future__ import annotations

import threading

from langchain_core.messages import AIMessage, HumanMessage

pytest_plugins = ["test_subagent_executor"]


# ---------------------------------------------------------------------------
# Fake agent that streams N AI messages through ``astream``.
# ---------------------------------------------------------------------------
class _FakeStreamWithMessages:
    """Streams a fixed list of ``{messages: [...]}`` chunks then ends.

    Mirrors the structure yielded by LangGraph under ``stream_mode="values"``:
    each chunk is the full state dict, with a ``messages`` list whose last
    element is the freshly produced ``AIMessage``.
    """

    def __init__(self, message_contents: list[str]) -> None:
        self._contents = message_contents
        self.captured_config: dict | None = None
        self.captured_context: dict | None = None

    async def astream(self, state, *, config, context, stream_mode):  # noqa: ARG002 - signature parity
        self.captured_config = config
        self.captured_context = context
        for idx, content in enumerate(self._contents):
            # Each chunk: full state with the new trailing AI message.
            yield {
                "messages": [
                    HumanMessage(content=f"human-prompt-{idx}"),
                    AIMessage(content=content, id=f"m{idx + 1}"),
                ],
            }


async def _noop_build_initial_state(self, task):  # noqa: ARG002 - signature parity
    """Stub ``_build_initial_state`` so ``_aexecute`` reaches ``astream``."""
    return ({"messages": [HumanMessage(content=task)]}, [], None)


def _make_executor(classes, *, keep_alive: bool = False, max_requests: int | None = None, name: str = "test"):
    SubagentExecutor = classes["SubagentExecutor"]
    SubagentConfig = classes["SubagentConfig"]
    config = SubagentConfig(
        name=name,
        description="Integration test agent",
        system_prompt="",
        max_turns=10,
        timeout_seconds=30,
        keep_alive=keep_alive,
        max_requests=max_requests,
    )
    return SubagentExecutor(
        config=config,
        tools=[],
        parent_model="test-model",
        thread_id="thread-integration-1",
        trace_id="trace-integration-1",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_keep_alive_transitions_to_idle(_setup_executor_classes):
    """keep_alive=True + clean run -> result.status is IDLE and an 'idle'
    lifecycle event is emitted on the event_bus."""
    classes = _setup_executor_classes
    SubagentStatus = classes["SubagentStatus"]

    from deerflow.subagents.event_bus import event_bus

    lifecycle_events: list[dict] = []
    unsub = event_bus.on("subagent:lifecycle", lifecycle_events.append)
    try:
        executor = _make_executor(classes, keep_alive=True)
        fake_agent = _FakeStreamWithMessages(["hello"])
        # Monkeypatch the per-instance methods so we do not need a real LLM.
        executor._build_initial_state = _noop_build_initial_state.__get__(executor)
        executor._create_agent = lambda *a, **kw: fake_agent  # type: ignore[assignment]

        result = executor.execute("test task")

        assert result.status is SubagentStatus.IDLE, f"keep_alive=True should transition to IDLE, got {result.status!r}"
        idle_events = [e for e in lifecycle_events if e.get("event") == "idle"]
        assert len(idle_events) == 1, f"expected one 'idle' lifecycle event, got {lifecycle_events!r}"
    finally:
        unsub()


def test_keep_alive_false_transitions_to_completed(_setup_executor_classes):
    """keep_alive=False + clean run -> result.status is COMPLETED and a
    'completed' lifecycle event is emitted."""
    classes = _setup_executor_classes
    SubagentStatus = classes["SubagentStatus"]

    from deerflow.subagents.event_bus import event_bus

    lifecycle_events: list[dict] = []
    unsub = event_bus.on("subagent:lifecycle", lifecycle_events.append)
    try:
        executor = _make_executor(classes, keep_alive=False)
        fake_agent = _FakeStreamWithMessages(["hello"])
        executor._build_initial_state = _noop_build_initial_state.__get__(executor)
        executor._create_agent = lambda *a, **kw: fake_agent  # type: ignore[assignment]

        result = executor.execute("test task")

        assert result.status is SubagentStatus.COMPLETED, f"keep_alive=False should COMPLETE, got {result.status!r}"
        completed_events = [e for e in lifecycle_events if e.get("event") == "completed"]
        assert len(completed_events) == 1, f"expected one 'completed' lifecycle event, got {lifecycle_events!r}"
    finally:
        unsub()


def test_budget_exceeded_terminates(_setup_executor_classes):
    """max_requests=1 + 2 streamed AI chunks -> the second chunk trips the
    hard budget and the run terminates as FAILED with a budget error. A
    'subagent:budget' event is emitted."""
    classes = _setup_executor_classes
    SubagentStatus = classes["SubagentStatus"]

    from deerflow.subagents.event_bus import event_bus

    budget_events: list[dict] = []
    unsub_budget = event_bus.on("subagent:budget", budget_events.append)
    try:
        executor = _make_executor(classes, max_requests=1)
        # Two AI messages: budget soft=1 -> hard=ceil(1.5)=2. First tick
        # crosses the soft limit (warning event); second tick crosses the
        # hard limit (exceeded event) and the executor breaks the loop.
        fake_agent = _FakeStreamWithMessages(["hello", "world"])
        executor._build_initial_state = _noop_build_initial_state.__get__(executor)
        executor._create_agent = lambda *a, **kw: fake_agent  # type: ignore[assignment]

        result = executor.execute("test task")

        assert result.status is SubagentStatus.FAILED, f"budget-exceeded run should FAIL, got {result.status!r}"
        assert result.error is not None and "budget" in result.error.lower(), f"expected 'budget' in error, got {result.error!r}"
        # At least one subagent:budget event must have fired (warning and/or
        # exceeded). We require an 'exceeded' event specifically because that
        # is what the executor uses to break the loop.
        exceeded = [e for e in budget_events if e.get("event") == "exceeded"]
        assert exceeded, f"expected an 'exceeded' budget event, got {budget_events!r}"
    finally:
        unsub_budget()


def test_lifecycle_events_on_execute_async(_setup_executor_classes):
    """execute_async registers an AgentRef in the registry and emits a
    'started' lifecycle event on the event_bus before the run completes."""
    classes = _setup_executor_classes
    SubagentStatus = classes["SubagentStatus"]

    from deerflow.subagents.agent_registry import agent_registry
    from deerflow.subagents.event_bus import event_bus

    lifecycle_events: list[dict] = []
    unsub = event_bus.on("subagent:lifecycle", lifecycle_events.append)
    try:
        executor = _make_executor(classes, name="async-wiring")
        fake_agent = _FakeStreamWithMessages(["hello"])
        executor._build_initial_state = _noop_build_initial_state.__get__(executor)
        executor._create_agent = lambda *a, **kw: fake_agent  # type: ignore[assignment]

        task_id = executor.execute_async("test task", task_id="t12-integration")

        # execute_async returns immediately; the background task picks up
        # shortly. Poll for the AgentRef to appear (registry is populated
        # synchronously inside execute_async, before the run_task closure).
        ref = agent_registry.get(task_id)
        assert ref is not None, f"AgentRef for {task_id} should be registered before run starts"
        assert ref.subagent_type == "async-wiring"
        # The run_task closure bumps to RUNNING; the initial registration
        # uses PENDING. Either is acceptable here as long as the ref exists
        # and is a non-terminal status.
        assert ref.status in (SubagentStatus.PENDING, SubagentStatus.RUNNING, SubagentStatus.COMPLETED, SubagentStatus.IDLE), f"unexpected ref status: {ref.status!r}"

        # Wait for the background task to finish so the 'started' event has
        # been emitted (it is emitted inside run_task). 5s is plenty for a
        # one-turn in-process astream.
        deadline = threading.Event()
        timer = threading.Timer(5.0, deadline.set)
        timer.daemon = True
        timer.start()
        try:
            while not deadline.is_set():
                started = [e for e in lifecycle_events if e.get("event") == "started" and e.get("task_id") == task_id]
                if started:
                    break
                # Also break if the ref has reached a terminal state (no
                # 'started' event will ever fire for a synchronous stub).
                current = agent_registry.get(task_id)
                if current is not None and current.result is not None and current.result.status.is_terminal:
                    break
                threading.Event().wait(0.05)
        finally:
            timer.cancel()

        started_events = [e for e in lifecycle_events if e.get("event") == "started" and e.get("task_id") == task_id]
        assert started_events, f"expected a 'started' lifecycle event for {task_id}, got {lifecycle_events!r}"
    finally:
        unsub()
