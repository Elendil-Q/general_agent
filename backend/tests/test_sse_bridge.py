"""Tests for the SSE bridge routing EventBus payloads to per-run writers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.subagents.event_bus import event_bus
from deerflow.subagents.sse_bridge import sse_bridge


def test_routes_lifecycle_event_to_registered_writer():
    sent: list[dict] = []
    sse_bridge.register_writer("T1", sent.append)
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "started", "task_id": "t1", "thread_id": "T1"},
    )
    assert sent and sent[0]["type"] == "task_started"
    assert sent[0]["task_id"] == "t1"
    sse_bridge.unregister_writer("T1")


def test_no_writer_drops_event_silently():
    # no writer registered for T2
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "completed", "task_id": "t2", "thread_id": "T2"},
    )
    # no assertion needed beyond not raising; state is in Registry anyway


def test_unregister_stops_delivery():
    sent: list[dict] = []
    sse_bridge.register_writer("T1", sent.append)
    sse_bridge.unregister_writer("T1")
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "started", "task_id": "t1", "thread_id": "T1"},
    )
    assert sent == []


def test_concurrent_writers_different_threads():
    a: list[dict] = []
    b: list[dict] = []
    sse_bridge.register_writer("TA", a.append)
    sse_bridge.register_writer("TB", b.append)
    event_bus.emit("subagent:lifecycle", {"event": "started", "thread_id": "TA"})
    event_bus.emit("subagent:lifecycle", {"event": "started", "thread_id": "TB"})
    assert len(a) == 1 and len(b) == 1
    sse_bridge.unregister_writer("TA")
    sse_bridge.unregister_writer("TB")


def test_budget_event_mapped():
    sent: list[dict] = []
    sse_bridge.register_writer("T1", sent.append)
    event_bus.emit(
        "subagent:budget",
        {"event": "warning", "task_id": "t1", "thread_id": "T1", "requests": 200, "limit": 200},
    )
    assert sent and sent[0]["type"] == "budget_warning"
    sse_bridge.unregister_writer("T1")


def test_unregister_skips_when_idle_subagents_exist():
    """Writer must stay alive while any non-terminal subagent remains for the thread."""
    sent: list[dict] = []
    sse_bridge.register_writer("T1", sent.append)
    # A non-terminal subagent (e.g. IDLE) keeps the writer alive.
    _non_terminal = SimpleNamespace(status=SimpleNamespace(is_terminal=False))
    with patch(
        "deerflow.subagents.agent_registry.agent_registry.list_by_thread",
        return_value=[_non_terminal],
    ):
        sse_bridge.unregister_writer("T1")
    # Writer should still be registered
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "expired", "task_id": "t1", "thread_id": "T1"},
    )
    assert sent and sent[0]["type"] == "task_expired"
    # Clean up
    with patch(
        "deerflow.subagents.agent_registry.agent_registry.list_by_thread",
        return_value=[],
    ):
        sse_bridge.unregister_writer("T1")


def test_unregister_releases_when_no_idle_subagents():
    """Writer is released once no non-terminal subagents remain."""
    sent: list[dict] = []
    sse_bridge.register_writer("T1", sent.append)
    with patch(
        "deerflow.subagents.agent_registry.agent_registry.list_by_thread",
        return_value=[],
    ):
        sse_bridge.unregister_writer("T1")
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "started", "task_id": "t1", "thread_id": "T1"},
    )
    assert sent == []


# ---------------------------------------------------------------------------
# Multi-subagent writer lifecycle
#
# conftest.py mocks ``deerflow.subagents.executor`` to break a circular import
# chain. The brief's multi-subagent test needs the *real* ``SubagentStatus``
# (so ``is_terminal`` resolves to a bool), so this fixture clears the mock and
# imports the real executor/registry fresh - same pattern as
# ``test_subagent_lifecycle.py`` and ``test_subagent_status_semantics.py``.
# ---------------------------------------------------------------------------

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


@pytest.fixture()
def _real_executor():
    """Load the real executor + agent_registry modules for one test.

    Restores the conftest executor mock and original agent_registry module on
    teardown so the lightweight routing tests above keep using the mock.
    """
    original_modules = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")
    original_registry = sys.modules.get("deerflow.subagents.agent_registry")
    original_pkg = sys.modules.get("deerflow.subagents")

    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    if "deerflow.subagents.agent_registry" in sys.modules:
        del sys.modules["deerflow.subagents.agent_registry"]
    _clear_stale_executor_package_attr()

    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()
    storage_module = ModuleType("deerflow.skills.storage")
    storage_module.get_or_new_skill_storage = lambda **kwargs: SimpleNamespace(load_skills=lambda *, enabled_only: [])
    sys.modules["deerflow.skills.storage"] = storage_module

    from deerflow.subagents.agent_registry import agent_registry  # noqa: F401
    from deerflow.subagents.executor import SubagentResult, SubagentStatus  # noqa: F401

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config
    agent_registry._refs.clear()

    yield

    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        elif name in sys.modules:
            del sys.modules[name]
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    if original_registry is not None:
        sys.modules["deerflow.subagents.agent_registry"] = original_registry
    elif "deerflow.subagents.agent_registry" in sys.modules:
        del sys.modules["deerflow.subagents.agent_registry"]
    if original_pkg is not None and "deerflow.subagents" not in sys.modules:
        sys.modules["deerflow.subagents"] = original_pkg


def test_unregister_writer_keeps_writer_when_other_subagents_running(_real_executor):
    """unregister_writer must not remove writer if any non-terminal subagents exist."""
    from datetime import datetime

    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.executor import SubagentResult, SubagentStatus
    from deerflow.subagents.sse_bridge import SSEBridge

    agent_registry._refs.clear()
    bridge = SSEBridge()
    writer_called = []
    bridge.register_writer("thread-1", lambda e: writer_called.append(e))

    # Subagent A is COMPLETED (terminal), B is still RUNNING
    agent_registry.register(
        AgentRef(
            task_id="A",
            thread_id="thread-1",
            trace_id="",
            subagent_type="gp",
            status=SubagentStatus.COMPLETED,
            config=None,
            executor=None,
            result=SubagentResult(task_id="A", trace_id="", status=SubagentStatus.COMPLETED),
            description="",
            created_at=datetime.now(),
        )
    )
    agent_registry.register(
        AgentRef(
            task_id="B",
            thread_id="thread-1",
            trace_id="",
            subagent_type="gp",
            status=SubagentStatus.RUNNING,
            config=None,
            executor=None,
            result=SubagentResult(task_id="B", trace_id="", status=SubagentStatus.RUNNING),
            description="",
            created_at=datetime.now(),
        )
    )

    bridge.unregister_writer("thread-1")
    # Writer must still be registered because B is RUNNING
    assert "thread-1" in bridge._writers

    # Now mark B as COMPLETED
    agent_registry.update_status("B", SubagentStatus.COMPLETED)
    bridge.unregister_writer("thread-1")
    # Now writer should be removed
    assert "thread-1" not in bridge._writers
