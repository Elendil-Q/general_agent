"""Core behavior tests for task tool orchestration."""

import asyncio
import importlib
import inspect
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock

from deerflow.subagents.config import SubagentConfig

# Use module import so tests can patch the exact symbols referenced inside task_tool().
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


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
