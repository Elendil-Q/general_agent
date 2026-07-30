"""Core behavior tests for task tool orchestration."""

import asyncio
import importlib
import inspect
import json
import sys
from datetime import datetime
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock

from deerflow.subagents.config import SubagentConfig

# Use module import so tests can patch the exact symbols referenced inside task_tool().
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")
wait_for_tasks_module = importlib.import_module("deerflow.tools.builtins.wait_for_tasks")


class FakeSubagentStatus(Enum):
    # Match production enum values so branch comparisons behave identically.
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    IDLE = "idle"

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
        return self.is_terminal or self in {
            type(self).INTERRUPTED,
            type(self).IDLE,
        }


def _make_runtime(*, app_config=None) -> SimpleNamespace:
    # Minimal ToolRuntime-like object; task_tool only reads these three attributes.
    context = {"thread_id": "thread-1"}
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
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )


def _make_subagent_config(name: str = "general-purpose") -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
    )


def _run_task_tool(**kwargs) -> str:
    """Execute the task tool across LangChain sync/async wrapper variants."""
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


def _wire_unified_mocks(monkeypatch, *, captured=None):
    """Patch symbol table so task_tool can run the unified path without real infra."""
    import deerflow.tools  # noqa: F401 - ensure module is importable for monkeypatch

    class DummyExecutor:
        def __init__(self, **kwargs):
            if captured is not None:
                captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            if captured is not None:
                captured["prompt"] = prompt
                captured["task_id"] = task_id
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: _make_subagent_config(name))
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **kw: ["general-purpose"])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kw: [])


# ---------------------------------------------------------------------------
# Unified-path tests
# ---------------------------------------------------------------------------


def test_task_has_no_detached_param():
    """task() must not accept detached parameter."""
    # @tool decorator wraps the function; inspect the underlying coroutine.
    func = getattr(task_tool_module.task_tool, "coroutine", None) or getattr(task_tool_module.task_tool, "func", None)
    assert func is not None
    sig = inspect.signature(func)
    assert "detached" not in sig.parameters


def test_task_always_returns_immediately(monkeypatch):
    """task() must always return immediately, never block."""
    _wire_unified_mocks(monkeypatch)

    result = _run_task_tool(
        runtime=_make_runtime(),
        description="test task",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-immediate",
    )

    assert "task_id=" in result, f"Expected 'task_id=' in result, got: {result!r}"
    assert "Task Succeeded" not in result
    assert "Task failed" not in result
    assert "wait_for_tasks" in result


def test_task_always_registers_writer(monkeypatch):
    """task() must always register SSE writer, even without detached=True."""
    register_calls: list[str] = []

    _wire_unified_mocks(monkeypatch)
    monkeypatch.setattr(
        task_tool_module.sse_bridge,
        "register_writer",
        lambda tid, w: register_calls.append(tid),
    )

    _run_task_tool(
        runtime=_make_runtime(),
        description="test task",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-register",
    )

    assert "thread-1" in register_calls


def test_task_always_emits_started_event(monkeypatch):
    """task() must always emit a subagent:lifecycle 'started' event."""
    from deerflow.subagents.event_bus import event_bus

    _wire_unified_mocks(monkeypatch)

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
            tool_call_id="tc-emit",
        )
    finally:
        unsub()

    started = [e for e in events if e.get("event") == "started"]
    assert len(started) >= 1
    assert started[0]["task_id"] == "tc-emit"


def test_task_registers_agent_ref_running(monkeypatch):
    """task() must register an AgentRef with RUNNING status in agent_registry."""
    from deerflow.subagents.agent_registry import agent_registry

    # Clean registry before test
    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)

    _wire_unified_mocks(monkeypatch)

    _run_task_tool(
        runtime=_make_runtime(),
        description="register test",
        prompt="background",
        subagent_type="general-purpose",
        tool_call_id="tc-register-ref",
    )

    ref = agent_registry.get("tc-register-ref")
    assert ref is not None
    assert ref.status == FakeSubagentStatus.RUNNING
    assert ref.subagent_type == "general-purpose"
    assert ref.description == "register test"

    # Cleanup
    agent_registry.remove("tc-register-ref")


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_task_tool_returns_error_for_unknown_subagent(monkeypatch):
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: None)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **kw: ["general-purpose"])

    result = _run_task_tool(
        runtime=None,
        description="执行任务",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-1",
    )

    assert result == "Error: Unknown subagent type 'general-purpose'. Available: general-purpose"


# ---------------------------------------------------------------------------
# Config propagation tests (unified path returns immediately)
# ---------------------------------------------------------------------------


def test_task_tool_threads_runtime_app_config_to_subagent_dependencies(monkeypatch):
    app_config = object()
    config = _make_subagent_config(name="general-purpose")
    runtime = _make_runtime(app_config=app_config)
    captured = {}

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            captured["prompt"] = prompt
            return task_id or "generated-task-id"

    def fake_get_available_subagent_names(*, app_config, user_id=None):
        captured["names_app_config"] = app_config
        return ["general-purpose"]

    def fake_get_subagent_config(name, *, app_config, user_id=None):
        captured["config_lookup"] = (name, app_config)
        return config

    def fake_get_available_tools(**kwargs):
        captured["tools_kwargs"] = kwargs
        return ["tool-a"]

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", fake_get_available_subagent_names)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", fake_get_subagent_config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", fake_get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="运行命令",
        prompt="inspect files",
        subagent_type="general-purpose",
        tool_call_id="tc-explicit-config",
    )

    assert "task_id=" in output
    assert captured["names_app_config"] is app_config
    assert captured["config_lookup"] == ("general-purpose", app_config)
    assert captured["tools_kwargs"]["app_config"] is app_config
    assert captured["executor_kwargs"]["app_config"] is app_config
    assert captured["executor_kwargs"]["tools"] == ["tool-a"]


def test_task_tool_propagates_tool_groups_to_subagent(monkeypatch):
    """Verify tool_groups from parent metadata are passed to get_available_tools(groups=...)."""
    config = _make_subagent_config()
    parent_tool_groups = ["file:read", "file:write", "bash"]
    runtime = SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {"workspace_path": "/tmp/workspace"},
        },
        context={"thread_id": "thread-1"},
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1", "tool_groups": parent_tool_groups}},
    )
    get_available_tools = MagicMock(return_value=["tool-a"])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="file work only",
        subagent_type="general-purpose",
        tool_call_id="tc-groups",
    )

    assert "task_id=" in output
    get_available_tools.assert_called_once_with(model_name="ark-model", groups=parent_tool_groups, subagent_enabled=False)


def test_task_tool_uses_subagent_model_override_for_tool_loading(monkeypatch):
    """Subagent model overrides should drive model-gated tool loading."""
    config = SubagentConfig(
        name="general-purpose",
        description="General helper",
        system_prompt="Base system prompt",
        model="vision-subagent-model",
        max_turns=50,
        timeout_seconds=10,
    )
    runtime = _make_runtime()
    runtime.config["metadata"]["model_name"] = "parent-text-model"
    get_available_tools = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="inspect image",
        prompt="inspect the uploaded image",
        subagent_type="general-purpose",
        tool_call_id="tc-issue-2543",
    )

    assert "task_id=" in output
    get_available_tools.assert_called_once_with(
        model_name="vision-subagent-model",
        groups=None,
        subagent_enabled=False,
    )


def test_task_tool_inherits_parent_skill_allowlist_for_default_subagent(monkeypatch):
    config = _make_subagent_config()
    runtime = _make_runtime()
    runtime.config["metadata"]["available_skills"] = ["safe-skill"]
    captured = {}

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["config"] = kwargs["config"]

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="use skills",
        subagent_type="general-purpose",
        tool_call_id="tc-skills",
    )

    assert "task_id=" in output
    assert captured["config"].skills == ["safe-skill"]


def test_task_tool_intersects_parent_and_subagent_skill_allowlists(monkeypatch):
    config = SubagentConfig(
        name="general-purpose",
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
        skills=["safe-skill", "other-skill"],
    )
    runtime = _make_runtime()
    runtime.config["metadata"]["available_skills"] = ["safe-skill"]
    captured = {}

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["config"] = kwargs["config"]

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="use skills",
        subagent_type="general-purpose",
        tool_call_id="tc-skills-intersection",
    )

    assert "task_id=" in output
    assert captured["config"].skills == ["safe-skill"]


def test_task_tool_no_tool_groups_passes_none(monkeypatch):
    """Verify that when metadata has no tool_groups, groups=None is passed (backward compat)."""
    config = _make_subagent_config()
    runtime = _make_runtime()
    get_available_tools = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="normal work",
        subagent_type="general-purpose",
        tool_call_id="tc-no-groups",
    )

    assert "task_id=" in output
    get_available_tools.assert_called_once_with(model_name="ark-model", groups=None, subagent_enabled=False)


def test_task_tool_runtime_none_passes_groups_none(monkeypatch):
    """Verify that when runtime is None, groups=None is passed."""
    config = _make_subagent_config()
    get_available_tools = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)
    fallback_app_config = SimpleNamespace(models=[SimpleNamespace(name="default-model")])
    monkeypatch.setattr(task_tool_module, "get_app_config", lambda: fallback_app_config)

    output = _run_task_tool(
        runtime=None,
        description="执行任务",
        prompt="no runtime",
        subagent_type="general-purpose",
        tool_call_id="tc-no-runtime",
    )

    assert "task_id=" in output
    get_available_tools.assert_called_once_with(
        model_name="default-model",
        groups=None,
        subagent_enabled=False,
        app_config=fallback_app_config,
    )


# ---------------------------------------------------------------------------
# Token usage cache utility tests
# ---------------------------------------------------------------------------


def test_subagent_usage_cache_is_skipped_when_config_file_is_missing(monkeypatch):
    monkeypatch.setattr(
        task_tool_module,
        "get_app_config",
        MagicMock(side_effect=FileNotFoundError("missing config")),
    )

    assert task_tool_module._token_usage_cache_enabled(None) is False


def test_subagent_usage_cache_is_skipped_when_token_usage_is_disabled(monkeypatch):
    config = _make_subagent_config()
    app_config = SimpleNamespace(token_usage=SimpleNamespace(enabled=False))
    runtime = _make_runtime(app_config=app_config)

    task_tool_module._subagent_usage_cache.clear()
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda *, app_config, **kw: ["general-purpose"])
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name, **kw: config)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    _run_task_tool(
        runtime=runtime,
        description="test",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-disabled-cache",
    )

    assert task_tool_module.pop_cached_subagent_usage("tc-disabled-cache") is None


# ---------------------------------------------------------------------------
# wait_for_tasks tests
# ---------------------------------------------------------------------------


def _run_wait_for_tasks(**kwargs) -> str:
    """Execute wait_for_tasks across LangChain sync/async wrapper variants."""
    coroutine = getattr(wait_for_tasks_module.wait_for_tasks, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return wait_for_tasks_module.wait_for_tasks.func(**kwargs)


def _wire_wait_for_tasks_mocks(monkeypatch):
    """Patch symbols so wait_for_tasks can run without real infra.

    The conftest pre-mocks ``deerflow.subagents.executor`` as a MagicMock to
    break a circular import. ``wait_for_tasks`` imports ``SubagentStatus`` and
    ``cleanup_background_task`` from it inside the function body, so we must
    patch the mock module's attributes for the function to pick up real
    behaviour.
    """
    executor_mock = sys.modules.get("deerflow.subagents.executor")
    if executor_mock is not None:
        monkeypatch.setattr(executor_mock, "SubagentStatus", FakeSubagentStatus)
        monkeypatch.setattr(executor_mock, "cleanup_background_task", _cleanup_background_task)
    monkeypatch.setattr(wait_for_tasks_module, "get_stream_writer", lambda: lambda _e: None)


def _cleanup_background_task(task_id: str) -> None:
    """Mimic the real cleanup_background_task using the real agent_registry."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    if ref is None or ref.result is None:
        return
    if ref.result.status.is_terminal:
        agent_registry.remove(task_id)


def _make_wait_agent_ref(
    task_id: str,
    status: FakeSubagentStatus,
    *,
    thread_id: str = "thread-1",
    result_val: str | None = None,
    error_val: str | None = None,
    timeout_seconds: int = 10,
):
    """Create an AgentRef for wait_for_tasks tests."""
    from deerflow.subagents.agent_registry import AgentRef

    config = SubagentConfig(
        name="general-purpose",
        description="test",
        system_prompt="test",
        max_turns=50,
        timeout_seconds=timeout_seconds,
    )
    result = SimpleNamespace(
        status=status,
        result=result_val,
        error=error_val,
    )
    return AgentRef(
        task_id=task_id,
        thread_id=thread_id,
        trace_id="trace-1",
        subagent_type="general-purpose",
        status=status,
        config=config,
        executor=None,
        result=result,
        description="test task",
        created_at=datetime.now(),
    )


def _clean_registry():
    """Remove all entries from the agent_registry."""
    from deerflow.subagents.agent_registry import agent_registry

    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)


def test_wait_for_tasks_polls_through_interrupted(monkeypatch):
    """wait_for_tasks must NOT return immediately for INTERRUPTED status."""
    from deerflow.subagents.agent_registry import agent_registry

    _clean_registry()
    try:
        task_id = "wft-polls-interrupted"
        ref = _make_wait_agent_ref(task_id, FakeSubagentStatus.INTERRUPTED)
        agent_registry.register(ref)

        _wire_wait_for_tasks_mocks(monkeypatch)

        poll_count = 0

        async def counting_sleep(_seconds):
            nonlocal poll_count
            poll_count += 1
            r = agent_registry.get(task_id)
            if r and r.status == FakeSubagentStatus.INTERRUPTED:
                r.status = FakeSubagentStatus.COMPLETED
                r.result.status = FakeSubagentStatus.COMPLETED
                r.result.result = "done"

        monkeypatch.setattr(wait_for_tasks_module, "asyncio", SimpleNamespace(sleep=counting_sleep))

        result_str = _run_wait_for_tasks(
            task_ids=[task_id],
            tool_call_id="tc-polls",
            runtime=_make_runtime(),
        )

        assert poll_count >= 1, "wait_for_tasks should poll (sleep) at least once for INTERRUPTED"
        results = json.loads(result_str)
        assert results[task_id]["status"] == "completed"
    finally:
        _clean_registry()


def test_wait_for_tasks_suspends_timeout_during_interrupted(monkeypatch):
    """wait_for_tasks must not advance timeout counter while INTERRUPTED."""
    from deerflow.subagents.agent_registry import agent_registry

    _clean_registry()
    try:
        task_id = "wft-suspend-timeout"
        ref = _make_wait_agent_ref(task_id, FakeSubagentStatus.INTERRUPTED, timeout_seconds=0)
        agent_registry.register(ref)

        _wire_wait_for_tasks_mocks(monkeypatch)
        monkeypatch.setattr(wait_for_tasks_module, "DEFAULT_TIMEOUT_SECONDS", 1)
        monkeypatch.setattr(wait_for_tasks_module, "_TIMEOUT_BUFFER_SECONDS", 0)
        monkeypatch.setattr(wait_for_tasks_module, "DEFAULT_POLL_SECONDS", 1)

        poll_count = 0

        async def counting_sleep(_seconds):
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 3:
                r = agent_registry.get(task_id)
                if r and r.status == FakeSubagentStatus.INTERRUPTED:
                    r.status = FakeSubagentStatus.COMPLETED
                    r.result.status = FakeSubagentStatus.COMPLETED
                    r.result.result = "done"

        monkeypatch.setattr(wait_for_tasks_module, "asyncio", SimpleNamespace(sleep=counting_sleep))

        result_str = _run_wait_for_tasks(
            task_ids=[task_id],
            tool_call_id="tc-suspend",
            runtime=_make_runtime(),
        )

        assert poll_count >= 3, f"wait_for_tasks should poll 3+ times with suspended timeout, got {poll_count}"
        results = json.loads(result_str)
        assert results[task_id]["status"] == "completed"
    finally:
        _clean_registry()


def test_wait_for_tasks_returns_for_idle(monkeypatch):
    """wait_for_tasks must return immediately for IDLE status."""
    from deerflow.subagents.agent_registry import agent_registry

    _clean_registry()
    try:
        task_id = "wft-idle"
        ref = _make_wait_agent_ref(task_id, FakeSubagentStatus.IDLE, result_val="idle-result")
        agent_registry.register(ref)

        _wire_wait_for_tasks_mocks(monkeypatch)

        sleep_called = False

        async def tracking_sleep(_seconds):
            nonlocal sleep_called
            sleep_called = True

        monkeypatch.setattr(wait_for_tasks_module, "asyncio", SimpleNamespace(sleep=tracking_sleep))

        result_str = _run_wait_for_tasks(
            task_ids=[task_id],
            tool_call_id="tc-idle",
            runtime=_make_runtime(),
        )

        assert not sleep_called, "wait_for_tasks should return immediately for IDLE (no sleep)"
        results = json.loads(result_str)
        assert results[task_id]["status"] == "idle"
        assert results[task_id]["result"] == "idle-result"
    finally:
        _clean_registry()


def test_wait_for_tasks_no_duplicate_lifecycle_events(monkeypatch):
    """wait_for_tasks must NOT emit subagent:lifecycle events (executor is sole emitter)."""
    from deerflow.subagents.agent_registry import agent_registry
    from deerflow.subagents.event_bus import event_bus

    _clean_registry()
    try:
        task_id = "wft-no-emit"
        ref = _make_wait_agent_ref(task_id, FakeSubagentStatus.COMPLETED, result_val="done")
        agent_registry.register(ref)

        _wire_wait_for_tasks_mocks(monkeypatch)

        events: list[dict] = []
        unsub = event_bus.on("subagent:lifecycle", lambda payload: events.append(payload))
        try:
            _run_wait_for_tasks(
                task_ids=[task_id],
                tool_call_id="tc-no-emit",
                runtime=_make_runtime(),
            )
        finally:
            unsub()

        assert len(events) == 0, f"wait_for_tasks should not emit lifecycle events, got: {events}"
    finally:
        _clean_registry()


def test_wait_for_tasks_cleans_up_terminal_tasks(monkeypatch):
    """wait_for_tasks must call cleanup_background_task for terminal tasks after returning."""
    from deerflow.subagents.agent_registry import agent_registry

    _clean_registry()
    try:
        task_id = "wft-cleanup"
        ref = _make_wait_agent_ref(task_id, FakeSubagentStatus.COMPLETED, result_val="done")
        agent_registry.register(ref)

        _wire_wait_for_tasks_mocks(monkeypatch)

        _run_wait_for_tasks(
            task_ids=[task_id],
            tool_call_id="tc-cleanup",
            runtime=_make_runtime(),
        )

        assert agent_registry.get(task_id) is None, "Terminal task should be cleaned up from registry"
    finally:
        _clean_registry()
