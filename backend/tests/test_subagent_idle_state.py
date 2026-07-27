"""Tests for the IDLE status and try_set_idle transition.

Note: Due to circular import issues in the main codebase, conftest.py mocks
deerflow.subagents.executor. This test file uses delayed import via fixture to
test the real implementation in isolation (same pattern as
test_subagent_status_semantics.py).
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    # For static analysis only; the real classes are imported lazily inside the
    # ``_setup_executor_classes`` fixture (conftest mocks the executor module).
    from deerflow.subagents.executor import SubagentResult, SubagentStatus

import pytest

# Module names that need to be mocked to break circular imports.
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
    """Import the real executor classes, bypassing conftest's mock.

    Mirrors the fixture in ``test_subagent_status_semantics.py``. The real
    ``SubagentResult`` / ``SubagentStatus`` are exposed as module globals so the
    test bodies (which reference them by bare name) work unchanged.
    """
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

    from deerflow.subagents.executor import SubagentResult, SubagentStatus

    # Expose real classes as module globals so the test bodies reference them
    # by bare name exactly as written.
    g = globals()
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


def _running_result() -> SubagentResult:
    return SubagentResult(task_id="t1", trace_id="tr", status=SubagentStatus.RUNNING)


def test_idle_is_not_terminal():
    assert not SubagentStatus.IDLE.is_terminal


def test_idle_is_stopped():
    assert SubagentStatus.IDLE.is_stopped


def test_try_set_idle_from_running_succeeds():
    r = _running_result()
    assert r.try_set_idle() is True
    assert r.status is SubagentStatus.IDLE
    assert r.idle_since is not None


def test_try_set_idle_refused_from_terminal():
    r = _running_result()
    r.try_set_terminal(SubagentStatus.COMPLETED, result="ok")
    assert r.try_set_idle() is False
    assert r.status is SubagentStatus.COMPLETED


def test_try_set_idle_refused_from_interrupted():
    r = _running_result()
    r.try_set_interrupted(subagent_thread_id="sub::T::t1")
    assert r.try_set_idle() is False
    assert r.status is SubagentStatus.INTERRUPTED


def test_try_set_idle_idempotent_from_idle():
    r = _running_result()
    r.try_set_idle()
    assert r.try_set_idle() is False  # already stopped
