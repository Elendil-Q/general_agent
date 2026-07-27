"""Tests for the AgentRegistry process-level state store.

Note: conftest.py mocks ``deerflow.subagents.executor`` with a MagicMock at
import time to break a circular import chain. A naive top-level
``from deerflow.subagents.executor import SubagentStatus`` would therefore bind
to the mock, and the registry module's own ``from ...executor import
SubagentStatus`` would bind to the mock too -- so identity checks
(``r.status is SubagentStatus.IDLE``) would be meaningless. We mirror the
established workaround from ``test_subagent_status_semantics.py``: an autouse
fixture clears the mock, imports the real classes fresh, and exposes them as
module globals so the (verbatim) test bodies resolve real symbols.
"""

from __future__ import annotations

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Static placeholders for the symbols the autouse fixture imports fresh and
# reassigns at runtime (see ``_setup_registry_classes``). Declared here so
# static analyzers (ruff F821) can resolve the bare names used in the test
# bodies, which are kept verbatim from the task brief.
agent_registry = None
AgentRef = None
SubagentStatus = None
SubagentConfig = None

# Modules whose mocking breaks the executor circular-import chain (mirrors
# test_subagent_status_semantics.py exactly).
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
def _setup_registry_classes():
    """Clear the executor mock and import real registry/executor classes.

    Re-importing ``agent_registry`` fresh each test yields an isolated
    ``agent_registry`` singleton (no cross-test leakage) bound to the real
    ``SubagentStatus`` enum.
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

    from deerflow.subagents.agent_registry import (
        AgentRef,
        AgentRegistry,  # noqa: F401
        agent_registry,
    )
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import (
        SubagentExecutor,  # noqa: F401
        SubagentResult,  # noqa: F401
        SubagentStatus,
    )

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config

    # Expose real symbols as module globals so the verbatim test bodies (which
    # use bare names like ``agent_registry`` / ``SubagentStatus``) resolve them.
    g = globals()
    g["SubagentConfig"] = SubagentConfig
    g["SubagentStatus"] = SubagentStatus
    g["AgentRef"] = AgentRef
    g["agent_registry"] = agent_registry

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


def _make_ref(task_id: str = "t1", thread_id: str = "T1") -> AgentRef:
    return AgentRef(
        task_id=task_id,
        thread_id=thread_id,
        trace_id="tr",
        subagent_type="general-purpose",
        status=SubagentStatus.RUNNING,
        config=SubagentConfig(name="general-purpose", description="d"),
        executor=None,
        result=None,
        description="desc",
        created_at=datetime.now(),
        idle_since=None,
    )


def test_register_and_get():
    ref = _make_ref()
    agent_registry.register(ref)
    assert agent_registry.get("t1") is ref


def test_get_missing_returns_none():
    assert agent_registry.get("nope") is None


def test_list_by_thread():
    agent_registry.register(_make_ref("t1", "T1"))
    agent_registry.register(_make_ref("t2", "T1"))
    agent_registry.register(_make_ref("t3", "T2"))
    ids = {r.task_id for r in agent_registry.list_by_thread("T1")}
    assert ids == {"t1", "t2"}


def test_list_idle_filters_status():
    agent_registry.register(_make_ref("t1", "T1"))
    agent_registry.update_status("t1", SubagentStatus.IDLE, idle_since=datetime.now())
    agent_registry.register(_make_ref("t2", "T1"))
    idle = {r.task_id for r in agent_registry.list_idle("T1")}
    assert idle == {"t1"}


def test_update_status_fires_change_event():
    agent_registry.register(_make_ref("t1", "T1"))
    seen: list = []
    unsub = agent_registry.on_status_change(lambda r: seen.append(r.status))
    agent_registry.update_status("t1", SubagentStatus.IDLE, idle_since=datetime.now())
    assert seen == [SubagentStatus.IDLE]
    unsub()


def test_remove():
    agent_registry.register(_make_ref("t1", "T1"))
    agent_registry.remove("t1")
    assert agent_registry.get("t1") is None
