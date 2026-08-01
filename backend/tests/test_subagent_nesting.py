"""Tests for nested subagents (a subagent spawning its own subagent).

Covers: lineage fields (parent_task_id/depth) on AgentRef + event payloads +
state mirror, the allow_subagents config gate, the depth limit, the nested
concurrency guard, and the SSE writer clobber guard.

Note: conftest.py pre-mocks ``deerflow.subagents.executor`` to break a circular
import. This module needs the REAL executor (real SubagentStatus enum,
SubagentExecutor._emit/_apply_runtime_context), so an autouse fixture swaps it
in following the pattern from test_subagent_executor.py.
"""

import asyncio
import importlib
import sys
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import tool

from deerflow.subagents.agent_registry import AgentRef, agent_registry
from deerflow.subagents.config import SubagentConfig

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")

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

# Populated by the _real_executor fixture.
_REAL: dict = {}


def _clear_stale_executor_package_attr() -> None:
    subagents_pkg = sys.modules.get("deerflow.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")


@pytest.fixture(autouse=True)
def _real_executor():
    original_modules = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")
    # state_mirror binds SubagentStatus into _STATUS_MAP at import time, so it
    # must be re-imported while the real executor is installed.
    original_state_mirror = sys.modules.pop("deerflow.subagents.state_mirror", None)

    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    _clear_stale_executor_package_attr()

    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()
    storage_module = ModuleType("deerflow.skills.storage")
    storage_module.get_or_new_skill_storage = lambda **kwargs: SimpleNamespace(load_skills=lambda *, enabled_only: [])
    sys.modules["deerflow.skills.storage"] = storage_module

    import deerflow.subagents.executor as real_executor

    real_executor.get_app_config = lambda: SimpleNamespace(tool_search=SimpleNamespace(enabled=False))
    _REAL["module"] = real_executor
    _REAL["SubagentStatus"] = real_executor.SubagentStatus

    yield real_executor

    # Restore mocks/originals so other test files are unaffected.
    for name, original in original_modules.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    else:
        sys.modules.pop("deerflow.subagents.executor", None)
    if original_state_mirror is not None:
        sys.modules["deerflow.subagents.state_mirror"] = original_state_mirror
    else:
        sys.modules.pop("deerflow.subagents.state_mirror", None)
    _clear_stale_executor_package_attr()
    _REAL.clear()


@pytest.fixture(autouse=True)
def _clean_registry():
    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)
    yield
    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)


@tool("task", parse_docstring=False)
def _dummy_task_tool(description: str, prompt: str, subagent_type: str) -> str:
    """Dummy task tool."""
    return "ok"


def _make_config(name: str = "general-purpose", **overrides) -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description="helper",
        system_prompt="prompt",
        max_turns=5,
        timeout_seconds=10,
        **overrides,
    )


def _make_ref(task_id: str, *, status=None, parent_task_id=None, depth=1) -> AgentRef:
    return AgentRef(
        task_id=task_id,
        thread_id="thread-1",
        trace_id="trace",
        subagent_type="general-purpose",
        status=status if status is not None else _REAL["SubagentStatus"].RUNNING,
        config=_make_config(),
        executor=None,
        result=None,
        description="desc",
        created_at=datetime.now(UTC),
        parent_task_id=parent_task_id,
        depth=depth,
    )


# ---------------------------------------------------------------------------
# Config gate
# ---------------------------------------------------------------------------


def test_allow_subagents_defaults_to_false():
    assert _make_config().allow_subagents is False


def test_executor_keeps_task_tool_when_nesting_allowed(_real_executor):
    executor = _real_executor.SubagentExecutor(
        config=_make_config(allow_subagents=True),
        tools=[_dummy_task_tool],
        thread_id="thread-1",
        task_id="task-1",
    )
    assert "task" in [t.name for t in executor._base_tools]


def test_executor_filters_task_tool_by_default(_real_executor):
    executor = _real_executor.SubagentExecutor(
        config=_make_config(),
        tools=[_dummy_task_tool],
        thread_id="thread-1",
        task_id="task-1",
    )
    assert "task" not in [t.name for t in executor._base_tools]


# ---------------------------------------------------------------------------
# Registry lineage
# ---------------------------------------------------------------------------


def test_agent_ref_lineage_defaults():
    ref = AgentRef(
        task_id="t-1",
        thread_id="thread-1",
        trace_id="trace",
        subagent_type="general-purpose",
        status=_REAL["SubagentStatus"].RUNNING,
        config=_make_config(),
        executor=None,
        result=None,
        description="desc",
        created_at=datetime.now(UTC),
    )
    assert ref.parent_task_id is None
    assert ref.depth == 1


def test_count_active_children():
    agent_registry.register(_make_ref("child-1", parent_task_id="parent-1", depth=2))
    agent_registry.register(_make_ref("child-2", parent_task_id="parent-1", depth=2))
    agent_registry.register(_make_ref("child-done", parent_task_id="parent-1", depth=2, status=_REAL["SubagentStatus"].COMPLETED))
    agent_registry.register(_make_ref("other", parent_task_id="parent-2", depth=2))

    assert agent_registry.count_active_children("parent-1") == 2
    assert agent_registry.count_active_children("parent-2") == 1
    assert agent_registry.count_active_children("nobody") == 0


# ---------------------------------------------------------------------------
# State mirror lineage
# ---------------------------------------------------------------------------


def test_state_mirror_includes_parent_task_id():
    from deerflow.subagents.state_mirror import build_subagents_snapshot

    ref = _make_ref("child-1", parent_task_id="parent-1", depth=2)
    snapshot = build_subagents_snapshot([ref], None, now=1000.0, ttl_seconds=60.0)
    assert snapshot is not None
    assert snapshot["child-1"]["parent_task_id"] == "parent-1"


def test_state_mirror_parent_task_id_defaults_to_none():
    from deerflow.subagents.state_mirror import build_subagents_snapshot

    ref = _make_ref("top-1")
    snapshot = build_subagents_snapshot([ref], None, now=1000.0, ttl_seconds=60.0)
    assert snapshot is not None
    assert snapshot["top-1"]["parent_task_id"] is None


# ---------------------------------------------------------------------------
# Executor lineage: emit + runtime context
# ---------------------------------------------------------------------------


def _make_executor(_real_executor, **kwargs):
    kwargs.setdefault("config", _make_config())
    kwargs.setdefault("tools", [])
    kwargs.setdefault("thread_id", "thread-1")
    kwargs.setdefault("task_id", "task-1")
    return _real_executor.SubagentExecutor(**kwargs)


def test_executor_emit_injects_lineage(_real_executor):
    from deerflow.subagents.event_bus import event_bus

    executor = _make_executor(_real_executor, parent_task_id="parent-1", depth=2)
    captured: list[dict] = []
    unsub = event_bus.on("subagent:lifecycle", captured.append)
    try:
        executor._emit("subagent:lifecycle", {"event": "started", "task_id": "task-1", "thread_id": "thread-1"})
    finally:
        unsub()
    assert captured[0]["parent_task_id"] == "parent-1"
    assert captured[0]["depth"] == 2


def test_executor_emit_lineage_defaults_for_top_level(_real_executor):
    from deerflow.subagents.event_bus import event_bus

    executor = _make_executor(_real_executor)
    captured: list[dict] = []
    unsub = event_bus.on("subagent:lifecycle", captured.append)
    try:
        executor._emit("subagent:lifecycle", {"event": "started", "task_id": "task-1", "thread_id": "thread-1"})
    finally:
        unsub()
    assert captured[0]["parent_task_id"] is None
    assert captured[0]["depth"] == 1


def test_apply_runtime_context_exposes_lineage_and_nesting_permission(_real_executor):
    executor = _make_executor(
        _real_executor,
        config=_make_config(allow_subagents=True),
        parent_task_id="parent-1",
        depth=1,
        task_id="task-9",
    )
    ctx: dict = {}
    executor._apply_runtime_context(ctx)
    assert ctx["is_subagent"] is True
    assert ctx["subagent_task_id"] == "task-9"
    assert ctx["subagent_depth"] == 1
    assert ctx["nested_subagents_allowed"] is True


def test_apply_runtime_context_denies_nesting_at_max_depth(_real_executor):
    executor = _make_executor(
        _real_executor,
        config=_make_config(allow_subagents=True),
        depth=_real_executor.MAX_SUBAGENT_DEPTH,
        task_id="task-deep",
    )
    ctx: dict = {}
    executor._apply_runtime_context(ctx)
    assert ctx["nested_subagents_allowed"] is False


def test_apply_runtime_context_denies_nesting_without_config_opt_in(_real_executor):
    executor = _make_executor(_real_executor, depth=1, task_id="task-1")
    ctx: dict = {}
    executor._apply_runtime_context(ctx)
    assert ctx["nested_subagents_allowed"] is False


# ---------------------------------------------------------------------------
# Config plumbing: YAML files + config.yaml custom_agents
# ---------------------------------------------------------------------------


def test_parse_subagent_file_parses_allow_subagents(tmp_path):
    from deerflow.subagents.storage import parse_subagent_file

    yaml_file = tmp_path / "nester.yaml"
    yaml_file.write_text("name: nester\ndescription: delegates further\nallow_subagents: true\n")
    config = parse_subagent_file(yaml_file)
    assert config is not None
    assert config.allow_subagents is True


def test_parse_subagent_file_allow_subagents_defaults_false(tmp_path):
    from deerflow.subagents.storage import parse_subagent_file

    yaml_file = tmp_path / "plain.yaml"
    yaml_file.write_text("name: plain\ndescription: no nesting\n")
    config = parse_subagent_file(yaml_file)
    assert config is not None
    assert config.allow_subagents is False


def test_custom_agents_config_maps_allow_subagents():
    from deerflow.config.subagents_config import CustomSubagentConfig, SubagentsAppConfig
    from deerflow.subagents.registry import _build_custom_subagent_config

    app_config = SubagentsAppConfig(
        custom_agents={
            "nester": CustomSubagentConfig(description="d", system_prompt="p", allow_subagents=True),
            "plain": CustomSubagentConfig(description="d", system_prompt="p"),
        }
    )
    assert _build_custom_subagent_config("nester", app_config=app_config).allow_subagents is True
    assert _build_custom_subagent_config("plain", app_config=app_config).allow_subagents is False


def _make_nested_runtime(*, nested_allowed=True, depth=1, parent_task_id="parent-1") -> SimpleNamespace:
    context = {
        "thread_id": "thread-1",
        "is_subagent": True,
        "subagent_task_id": parent_task_id,
        "subagent_depth": depth,
    }
    if nested_allowed:
        context["nested_subagents_allowed"] = True
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


def _wire_mocks(monkeypatch, *, captured=None, allow_subagents=True):
    import deerflow.tools  # noqa: F401

    class DummyExecutor:
        def __init__(self, **kwargs):
            if captured is not None:
                captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    tools_calls: list[dict] = []

    def fake_get_available_tools(**kwargs):
        tools_calls.append(kwargs)
        return []

    if captured is not None:
        captured["tools_calls"] = tools_calls

    monkeypatch.setattr(task_tool_module, "SubagentStatus", _REAL["SubagentStatus"])
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(
        task_tool_module,
        "get_subagent_config",
        lambda name, **kw: _make_config(name, allow_subagents=allow_subagents),
    )
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **kw: ["general-purpose"])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", fake_get_available_tools)


def _run_task_tool(**kwargs) -> str:
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


def test_nested_spawn_threads_lineage(monkeypatch):
    from deerflow.subagents.event_bus import event_bus

    captured: dict = {}
    _wire_mocks(monkeypatch, captured=captured)

    register_calls: list[str] = []
    monkeypatch.setattr(task_tool_module.sse_bridge, "register_writer", lambda tid, w: register_calls.append(tid))

    events: list[dict] = []
    unsub = event_bus.on("subagent:lifecycle", events.append)
    try:
        result = _run_task_tool(
            runtime=_make_nested_runtime(),
            description="nested task",
            prompt="do work",
            subagent_type="general-purpose",
            tool_call_id="tc-nested",
        )
    finally:
        unsub()

    assert "task_id=" in result
    assert captured["executor_kwargs"]["parent_task_id"] == "parent-1"
    assert captured["executor_kwargs"]["depth"] == 2
    assert captured["tools_calls"][0]["subagent_enabled"] is True
    # A nested run must NOT overwrite the lead run's SSE writer.
    assert register_calls == []
    started = [e for e in events if e.get("event") == "started"]
    assert started[0]["parent_task_id"] == "parent-1"
    assert started[0]["depth"] == 2


def test_nested_spawn_blocked_without_permission(monkeypatch):
    captured: dict = {}
    _wire_mocks(monkeypatch, captured=captured)

    result = _run_task_tool(
        runtime=_make_nested_runtime(nested_allowed=False),
        description="nested task",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-blocked",
    )

    assert "not permitted" in result.lower() or "nesting" in result.lower()
    assert "executor_kwargs" not in captured


def test_nested_spawn_blocked_by_concurrency_guard(monkeypatch):
    captured: dict = {}
    _wire_mocks(monkeypatch, captured=captured)

    for i in range(3):
        agent_registry.register(_make_ref(f"busy-{i}", parent_task_id="parent-1", depth=2))

    result = _run_task_tool(
        runtime=_make_nested_runtime(),
        description="nested task",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-guard",
    )

    assert "concurrent" in result.lower() or "limit" in result.lower()
    assert "executor_kwargs" not in captured


def test_lead_spawn_registers_writer_with_null_lineage(monkeypatch):
    captured: dict = {}
    _wire_mocks(monkeypatch, captured=captured)

    register_calls: list[str] = []
    monkeypatch.setattr(task_tool_module.sse_bridge, "register_writer", lambda tid, w: register_calls.append(tid))

    runtime = SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context={"thread_id": "thread-1"},
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )
    _run_task_tool(
        runtime=runtime,
        description="top-level task",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-lead",
    )

    assert register_calls == ["thread-1"]
    assert captured["executor_kwargs"]["parent_task_id"] is None
    assert captured["executor_kwargs"]["depth"] == 1
    assert captured["tools_calls"][0]["subagent_enabled"] is False
