# backend/tests/test_subagent_registry_shim.py
"""The module-level accessors delegate to AgentRegistry, preserving signatures.

Note: conftest.py mocks ``deerflow.subagents.executor`` with a MagicMock at
import time to break a circular import chain. We mirror the established
workaround from ``test_agent_registry.py``: an autouse fixture clears the mock,
imports the real classes fresh, and exposes them as module globals so the
(verbatim) test bodies resolve real symbols.
"""

from __future__ import annotations

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Static placeholders for the symbols the autouse fixture imports fresh and
# reassigns at runtime. Declared so static analyzers (ruff F821) can resolve
# the bare names used in the verbatim test bodies.
exec_mod = None
agent_registry = None
AgentRef = None
SubagentConfig = None
SubagentResult = None
SubagentStatus = None

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


@pytest.fixture(autouse=True)
def _setup_executor_classes():
    """Clear the executor mock and import real registry/executor classes.

    Re-importing ``agent_registry`` fresh each test yields an isolated
    ``agent_registry`` singleton (no cross-test leakage) bound to the real
    ``SubagentStatus`` enum. The real executor module is exposed as ``exec_mod``
    so the verbatim test bodies resolve it.
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

    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentResult, SubagentStatus

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config

    # Expose real symbols as module globals so the verbatim test bodies (which
    # use bare names like ``exec_mod`` / ``agent_registry``) resolve them.
    g = globals()
    g["exec_mod"] = executor_module
    g["agent_registry"] = agent_registry
    g["AgentRef"] = AgentRef
    g["SubagentConfig"] = SubagentConfig
    g["SubagentResult"] = SubagentResult
    g["SubagentStatus"] = SubagentStatus

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


def _seed(task_id: str = "t1") -> SubagentResult:
    result = SubagentResult(task_id=task_id, trace_id="tr", status=SubagentStatus.RUNNING)
    agent_registry.register(
        AgentRef(
            task_id=task_id,
            thread_id="T1",
            trace_id="tr",
            subagent_type="general-purpose",
            status=SubagentStatus.RUNNING,
            config=SubagentConfig(name="general-purpose", description="d"),
            executor=None,
            result=result,
            description="desc",
            created_at=datetime.now(),
        )
    )
    return result


def test_get_background_task_result_reads_registry():
    result = _seed("t1")
    assert exec_mod.get_background_task_result("t1") is result


def test_get_background_task_result_missing_returns_none():
    assert exec_mod.get_background_task_result("nope") is None


def test_list_background_tasks_returns_all_results():
    _seed("t1")
    _seed("t2")
    ids = {r.task_id for r in exec_mod.list_background_tasks()}
    assert ids >= {"t1", "t2"}


def test_cleanup_background_task_removes_from_registry_when_terminal():
    result = _seed("t1")
    result.try_set_terminal(SubagentStatus.COMPLETED, result="ok")
    exec_mod.cleanup_background_task("t1")
    assert agent_registry.get("t1") is None


def test_cleanup_keeps_idle_in_registry():
    result = _seed("t1")
    result.try_set_idle()
    exec_mod.cleanup_background_task("t1")
    assert agent_registry.get("t1") is not None  # IDLE not cleaned by cleanup


def test_get_background_task_result_uses_registry_only(monkeypatch):
    """get_background_task_result must not fall back to _background_tasks."""
    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.executor import SubagentResult, SubagentStatus, get_background_task_result

    agent_registry._refs.clear()
    result = SubagentResult(task_id="test-1", trace_id="t", status=SubagentStatus.COMPLETED)
    agent_registry.register(
        AgentRef(
            task_id="test-1",
            thread_id="th",
            trace_id="t",
            subagent_type="general-purpose",
            status=SubagentStatus.COMPLETED,
            config=None,
            executor=None,
            result=result,
            description="",
            created_at=datetime.now(),
        )
    )
    assert get_background_task_result("test-1") is result
    assert get_background_task_result("nonexistent") is None


def test_list_background_tasks_uses_registry_only():
    """list_background_tasks must not merge from _background_tasks."""
    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.executor import SubagentResult, SubagentStatus, list_background_tasks

    agent_registry._refs.clear()
    result = SubagentResult(task_id="test-2", trace_id="t", status=SubagentStatus.COMPLETED)
    agent_registry.register(
        AgentRef(
            task_id="test-2",
            thread_id="th",
            trace_id="t",
            subagent_type="general-purpose",
            status=SubagentStatus.COMPLETED,
            config=None,
            executor=None,
            result=result,
            description="",
            created_at=datetime.now(),
        )
    )
    tasks = list_background_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_id == "test-2"


def test_cleanup_background_task_uses_registry_only():
    """cleanup_background_task must not touch _background_tasks or _subagent_executors."""
    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.executor import SubagentResult, SubagentStatus, cleanup_background_task

    agent_registry._refs.clear()
    result = SubagentResult(task_id="test-3", trace_id="t", status=SubagentStatus.COMPLETED)
    result.try_set_terminal(SubagentStatus.COMPLETED, result="done")
    agent_registry.register(
        AgentRef(
            task_id="test-3",
            thread_id="th",
            trace_id="t",
            subagent_type="general-purpose",
            status=SubagentStatus.COMPLETED,
            config=None,
            executor=None,
            result=result,
            description="",
            created_at=datetime.now(),
        )
    )
    cleanup_background_task("test-3")
    assert agent_registry.get("test-3") is None


def test_background_tasks_dicts_no_longer_exist():
    """_background_tasks and _subagent_executors must not exist as module attributes."""
    import deerflow.subagents.executor as executor_module

    assert not hasattr(executor_module, "_background_tasks")
    assert not hasattr(executor_module, "_background_tasks_lock")
    assert not hasattr(executor_module, "_subagent_executors")
