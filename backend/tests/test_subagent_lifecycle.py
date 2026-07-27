"""Tests for IDLE TTL adoption and cleanup.

Note: conftest.py mocks ``deerflow.subagents.executor`` with a MagicMock at
import time to break a circular import chain. We mirror the established
workaround from ``test_subagent_registry_shim.py``: an autouse fixture clears
the mock, imports the real classes fresh, and exposes them as module globals
so the (verbatim) test bodies resolve real symbols.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    # For static analysis only; the real classes are imported lazily inside the
    # ``_setup_executor_classes`` fixture (conftest mocks the executor module).
    from deerflow.subagents.agent_registry import AgentRef, agent_registry
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentResult, SubagentStatus
    from deerflow.subagents.lifecycle import SubagentLifecycleManager

import pytest

# Static placeholders for the symbols the autouse fixture imports fresh and
# reassigns at runtime. Declared so static analyzers (ruff F821) can resolve
# the bare names used in the verbatim test bodies.
agent_registry = None
AgentRef = None
SubagentConfig = None
SubagentResult = None
SubagentStatus = None
SubagentLifecycleManager = None

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
    ``SubagentStatus`` enum. The real lifecycle manager class is exposed so the
    verbatim test bodies resolve it.
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
    from deerflow.subagents.lifecycle import SubagentLifecycleManager

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config

    # Expose real symbols as module globals so the verbatim test bodies (which
    # use bare names like ``agent_registry`` / ``SubagentLifecycleManager``)
    # resolve them.
    g = globals()
    g["agent_registry"] = agent_registry
    g["AgentRef"] = AgentRef
    g["SubagentConfig"] = SubagentConfig
    g["SubagentResult"] = SubagentResult
    g["SubagentStatus"] = SubagentStatus
    g["SubagentLifecycleManager"] = SubagentLifecycleManager

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


def _seed_idle(task_id: str = "t1") -> SubagentResult:
    result = SubagentResult(task_id=task_id, trace_id="tr", status=SubagentStatus.IDLE)
    result.idle_since = datetime.now()
    agent_registry.register(
        AgentRef(
            task_id=task_id,
            thread_id="T1",
            trace_id="tr",
            subagent_type="general-purpose",
            status=SubagentStatus.IDLE,
            config=SubagentConfig(name="general-purpose", description="d"),
            executor=None,
            result=result,
            description="d",
            created_at=datetime.now(),
            idle_since=datetime.now(),
        )
    )
    return result


def test_ttl_expiry_cleans_up():
    mgr = SubagentLifecycleManager()
    _seed_idle("t1")
    mgr.adopt("t1", ttl_seconds=0.05)  # 50ms
    time.sleep(0.1)
    assert agent_registry.get("t1") is None


def test_cancel_ttl_prevents_cleanup():
    mgr = SubagentLifecycleManager()
    _seed_idle("t1")
    mgr.adopt("t1", ttl_seconds=0.05)
    mgr.cancel_ttl("t1")
    time.sleep(0.1)
    assert agent_registry.get("t1") is not None


def test_dismiss_cleans_up_immediately():
    mgr = SubagentLifecycleManager()
    _seed_idle("t1")
    mgr.adopt("t1", ttl_seconds=60)
    mgr.dismiss("t1")
    assert agent_registry.get("t1") is None
