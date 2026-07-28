"""Tests for detached execution mode in the task tool.

Covers:
- ``task_tool(detached=True)`` returns immediately with a ``task_id=`` string.
- An ``AgentRef`` is registered in ``agent_registry`` with status RUNNING.
- A ``subagent:lifecycle`` "started" event is emitted via the EventBus.
- ``task_tool(detached=False)`` (default / backward-compat) still polls and
  returns ``Task Succeeded``.
"""

import asyncio
import importlib
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import pytest

from deerflow.subagents.agent_registry import agent_registry
from deerflow.subagents.event_bus import event_bus

pytest_plugins = ["test_subagent_executor"]

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeSubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            type(self).COMPLETED,
            type(self).FAILED,
            type(self).CANCELLED,
            type(self).TIMED_OUT,
        }

    @property
    def is_stopped(self) -> bool:
        return self.is_terminal or self is type(self).INTERRUPTED


def _make_runtime(app_config=None):
    """Minimal ToolRuntime-like object; task_tool reads these attributes."""
    context = {"thread_id": "thread-detached-test"}
    if app_config is not None:
        context["app_config"] = app_config
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context=context,
        config={"metadata": {"model_name": "test-model", "trace_id": "trace-detached"}},
    )


def _make_subagent_config(name="general-purpose"):
    from deerflow.subagents.config import SubagentConfig

    return SubagentConfig(
        name=name,
        description="Test subagent for detached mode",
        system_prompt="You are a test subagent.",
        max_turns=10,
        timeout_seconds=60,
    )


def _make_subagent_config_with_timeout(timeout_seconds: int):
    from deerflow.subagents.config import SubagentConfig

    return SubagentConfig(
        name="general-purpose",
        description="Test subagent",
        system_prompt="You are a test subagent.",
        max_turns=10,
        timeout_seconds=timeout_seconds,
    )


def _make_completed_result(result_text="done"):
    return SimpleNamespace(
        status=FakeSubagentStatus.COMPLETED,
        ai_messages=[],
        result=result_text,
        error=None,
        token_usage_records=[],
        usage_reported=False,
        interrupts=None,
        subagent_thread_id=None,
    )


async def _no_sleep(_: float) -> None:
    return None


def _run_task_tool(**kwargs) -> str:
    """Execute the task tool through LangChain's sync/async wrapper."""
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the process-global agent_registry before and after each test."""
    # Drain before test (in case a previous test leaked state).
    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)
    yield
    # Drain after test so we never leak between test files.
    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)


def _wire_detached_mocks(monkeypatch, captured=None):
    """Patch symbol table so task_tool can run detached without real infra."""

    class DummyExecutor:
        def __init__(self, **kwargs):
            if captured is not None:
                captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            if captured is not None:
                captured["execute_async_prompt"] = prompt
                captured["execute_async_task_id"] = task_id
            return task_id or "detached-task-123"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(
        task_tool_module,
        "get_subagent_config",
        lambda name, **kw: _make_subagent_config(name),
    )
    monkeypatch.setattr(
        task_tool_module,
        "get_available_subagent_names",
        lambda **kw: ["general-purpose"],
    )
    monkeypatch.setattr(task_tool_module, "is_host_bash_allowed", lambda: True)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kw: [])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detached_returns_immediately(monkeypatch):
    """``detached=True`` returns a ``task_id=`` string and does NOT poll."""
    _wire_detached_mocks(monkeypatch)

    result = _run_task_tool(
        runtime=_make_runtime(),
        description="do detached work",
        prompt="run in background",
        subagent_type="general-purpose",
        tool_call_id="tc-detached-1",
        detached=True,
    )

    assert "task_id=" in result, f"Expected 'task_id=' in result, got: {result!r}"
    assert "Task Succeeded" not in result
    assert "Task failed" not in result


def test_detached_registers_agent_ref(monkeypatch):
    """``detached=True`` registers an AgentRef with RUNNING status."""
    _wire_detached_mocks(monkeypatch)

    _run_task_tool(
        runtime=_make_runtime(),
        description="do detached work",
        prompt="run in background",
        subagent_type="general-purpose",
        tool_call_id="tc-detached-2",
        detached=True,
    )

    ref = agent_registry.get("tc-detached-2")
    assert ref is not None, "AgentRef must exist in agent_registry after detached spawn"
    assert ref.status == FakeSubagentStatus.RUNNING
    assert ref.subagent_type == "general-purpose"
    assert ref.description == "do detached work"


def test_detached_emits_started_event(monkeypatch):
    """``detached=True`` emits a ``subagent:lifecycle`` "started" event."""
    _wire_detached_mocks(monkeypatch)

    events: list[dict] = []

    def capture(payload: dict) -> None:
        events.append(payload)

    unsub = event_bus.on("subagent:lifecycle", capture)
    try:
        _run_task_tool(
            runtime=_make_runtime(),
            description="emit test",
            prompt="background",
            subagent_type="general-purpose",
            tool_call_id="tc-detached-3",
            detached=True,
        )
    finally:
        unsub()

    started_events = [e for e in events if e.get("event") == "started"]
    assert len(started_events) >= 1, f"Expected at least one 'started' event, got: {events}"
    assert started_events[0]["task_id"] == "tc-detached-3"


def test_blocking_mode_still_works(monkeypatch):
    """``detached=False`` (default) still polls and returns ``Task Succeeded``."""
    _wire_detached_mocks(monkeypatch)

    # Override the background task result to complete immediately.
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_completed_result("all good"),
    )
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)

    result = _run_task_tool(
        runtime=_make_runtime(),
        description="blocking work",
        prompt="do it now",
        subagent_type="general-purpose",
        tool_call_id="tc-blocking-1",
        detached=False,
    )

    assert "Task Succeeded" in result


def test_wait_for_tasks_collects_detached_results(monkeypatch, _setup_executor_classes):
    """``wait_for_tasks`` collects results of detached subagents as JSON."""
    import sys
    from datetime import datetime

    from deerflow.subagents.agent_registry import AgentRef
    from deerflow.subagents.executor import SubagentResult, SubagentStatus
    from deerflow.tools.builtins.wait_for_tasks import wait_for_tasks

    # Mock get_stream_writer to return a no-op writer
    monkeypatch.setattr(
        sys.modules["deerflow.tools.builtins.wait_for_tasks"],
        "get_stream_writer",
        lambda: lambda event: None,
    )

    # Register two completed AgentRefs
    tid_1 = "tid-wait-1"
    tid_2 = "tid-wait-2"
    for tid in (tid_1, tid_2):
        ref = AgentRef(
            task_id=tid,
            thread_id="thread-wait-test",
            trace_id=f"trace-{tid}",
            subagent_type="general-purpose",
            status=SubagentStatus.COMPLETED,
            config=_make_subagent_config(),
            executor=None,
            result=SubagentResult(
                task_id=tid,
                trace_id=f"trace-{tid}",
                status=SubagentStatus.COMPLETED,
                result=f"result-{tid}",
                error=None,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            ),
            description=f"detached work {tid}",
            created_at=datetime.now(UTC),
        )
        agent_registry.register(ref)

    runtime = _make_runtime()

    result_json = asyncio.run(
        wait_for_tasks.coroutine(
            task_ids=[tid_1, tid_2],
            tool_call_id="tc-wait-test",
            runtime=runtime,
        )
    )

    import json

    parsed = json.loads(result_json)
    assert tid_1 in parsed
    assert tid_2 in parsed
    assert parsed[tid_1]["status"] == "completed"
    assert parsed[tid_1]["result"] == "result-tid-wait-1"
    assert parsed[tid_2]["status"] == "completed"
    assert parsed[tid_2]["result"] == "result-tid-wait-2"


def test_wait_for_tasks_times_out(monkeypatch, _setup_executor_classes):
    """``wait_for_tasks`` returns partial results for timed-out subagents."""
    import sys

    from deerflow.subagents.agent_registry import AgentRef
    from deerflow.subagents.executor import SubagentResult, SubagentStatus
    from deerflow.tools.builtins.wait_for_tasks import wait_for_tasks

    _module = sys.modules["deerflow.tools.builtins.wait_for_tasks"]

    # Mock get_stream_writer to return a no-op writer
    monkeypatch.setattr(_module, "get_stream_writer", lambda: lambda event: None)
    # Make polling fast: 10ms polls, 0s timeout = 1 poll before timeout
    monkeypatch.setattr(_module, "DEFAULT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(_module, "DEFAULT_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(_module, "_TIMEOUT_BUFFER_SECONDS", 0)

    # One completed, one still RUNNING (never finishes)
    tid_done = "tid-wait-done"
    tid_pending = "tid-wait-pending"

    ref_done = AgentRef(
        task_id=tid_done,
        thread_id="thread-wait-timeout",
        trace_id="trace-done",
        subagent_type="general-purpose",
        status=SubagentStatus.COMPLETED,
        config=_make_subagent_config_with_timeout(0),
        executor=None,
        result=SubagentResult(
            task_id=tid_done,
            trace_id="trace-done",
            status=SubagentStatus.COMPLETED,
            result="done-result",
            error=None,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        ),
        description="completed work",
        created_at=datetime.now(UTC),
    )
    ref_pending = AgentRef(
        task_id=tid_pending,
        thread_id="thread-wait-timeout",
        trace_id="trace-pending",
        subagent_type="general-purpose",
        status=SubagentStatus.RUNNING,
        config=_make_subagent_config_with_timeout(0),
        result=None,
        executor=None,
        description="pending work",
        created_at=datetime.now(UTC),
    )
    agent_registry.register(ref_done)
    agent_registry.register(ref_pending)

    runtime = _make_runtime()

    result_json = asyncio.run(
        wait_for_tasks.coroutine(
            task_ids=[tid_done, tid_pending],
            tool_call_id="tc-wait-timeout",
            runtime=runtime,
        )
    )

    import json

    parsed = json.loads(result_json)
    assert tid_done in parsed
    assert parsed[tid_done]["status"] == "completed"
    assert parsed[tid_done]["result"] == "done-result"
    assert tid_pending in parsed
    assert parsed[tid_pending]["status"] == "pending"
    assert "timed out" in parsed[tid_pending]["error"]


def test_wait_for_tasks_emits_lifecycle_events(monkeypatch, _setup_executor_classes):
    """``wait_for_tasks`` emits lifecycle events for completed/failed tasks."""
    import sys
    from datetime import datetime

    from deerflow.subagents.agent_registry import AgentRef
    from deerflow.subagents.event_bus import event_bus
    from deerflow.subagents.executor import SubagentResult, SubagentStatus
    from deerflow.tools.builtins.wait_for_tasks import wait_for_tasks

    _module = sys.modules["deerflow.tools.builtins.wait_for_tasks"]
    monkeypatch.setattr(_module, "get_stream_writer", lambda: lambda event: None)

    tid_completed = "tid-emit-completed"
    tid_failed = "tid-emit-failed"

    ref_completed = AgentRef(
        task_id=tid_completed,
        thread_id="thread-emit-test",
        trace_id="trace-completed",
        subagent_type="general-purpose",
        status=SubagentStatus.COMPLETED,
        config=_make_subagent_config(),
        executor=None,
        result=SubagentResult(
            task_id=tid_completed,
            trace_id="trace-completed",
            status=SubagentStatus.COMPLETED,
            result="completed-result",
            error=None,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        ),
        description="completed work",
        created_at=datetime.now(UTC),
    )
    ref_failed = AgentRef(
        task_id=tid_failed,
        thread_id="thread-emit-test",
        trace_id="trace-failed",
        subagent_type="general-purpose",
        status=SubagentStatus.FAILED,
        config=_make_subagent_config(),
        executor=None,
        result=SubagentResult(
            task_id=tid_failed,
            trace_id="trace-failed",
            status=SubagentStatus.FAILED,
            result=None,
            error="task crashed",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        ),
        description="failed work",
        created_at=datetime.now(UTC),
    )
    agent_registry.register(ref_completed)
    agent_registry.register(ref_failed)

    events: list[dict] = []

    def capture(payload: dict) -> None:
        events.append(payload)

    unsub = event_bus.on("subagent:lifecycle", capture)
    try:
        asyncio.run(
            wait_for_tasks.coroutine(
                task_ids=[tid_completed, tid_failed],
                tool_call_id="tc-emit-test",
                runtime=_make_runtime(),
            )
        )
    finally:
        unsub()

    completed_events = [e for e in events if e.get("event") == "completed"]
    failed_events = [e for e in events if e.get("event") == "failed"]

    assert len(completed_events) == 1
    assert completed_events[0]["task_id"] == tid_completed
    assert completed_events[0]["result"] == "completed-result"

    assert len(failed_events) == 1
    assert failed_events[0]["task_id"] == tid_failed
    assert failed_events[0]["error"] == "task crashed"
