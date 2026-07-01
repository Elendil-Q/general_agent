"""Status-machine semantics for INTERRUPTED subagents.

Note: Due to circular import issues in the main codebase, conftest.py mocks
deerflow.subagents.executor. This test file uses delayed import via fixture to
test the real implementation in isolation (same pattern as test_subagent_executor.py).
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

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

    from deerflow.subagents.config import SubagentConfig  # noqa: F401
    from deerflow.subagents.executor import SubagentExecutor, SubagentResult, SubagentStatus

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config

    classes = {
        "SubagentExecutor": SubagentExecutor,
        "SubagentResult": SubagentResult,
        "SubagentStatus": SubagentStatus,
        "executor_module": executor_module,
    }
    yield classes

    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        elif name in sys.modules:
            del sys.modules[name]
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]


def _make_result(classes, status=None) -> "object":
    SubagentResult = classes["SubagentResult"]
    SubagentStatus = classes["SubagentStatus"]
    return SubagentResult(task_id="t1", trace_id="tr", status=status or SubagentStatus.RUNNING)


class TestInterruptedStatusSemantics:
    def test_interrupted_is_not_terminal(self, _setup_executor_classes):
        SubagentStatus = _setup_executor_classes["SubagentStatus"]
        assert SubagentStatus.INTERRUPTED.is_terminal is False

    def test_interrupted_is_stopped(self, _setup_executor_classes):
        SubagentStatus = _setup_executor_classes["SubagentStatus"]
        assert SubagentStatus.INTERRUPTED.is_stopped is True

    def test_terminal_states_remain_terminal_and_stopped(self, _setup_executor_classes):
        SubagentStatus = _setup_executor_classes["SubagentStatus"]
        for s in (SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT):
            assert s.is_terminal is True
            assert s.is_stopped is True

    def test_running_neither_terminal_nor_stopped(self, _setup_executor_classes):
        SubagentStatus = _setup_executor_classes["SubagentStatus"]
        assert SubagentStatus.RUNNING.is_terminal is False
        assert SubagentStatus.RUNNING.is_stopped is False

    def test_try_set_interrupted_from_running(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        result = _make_result(classes)
        ok = result.try_set_interrupted(interrupts=[{"value": "q", "id": "i1"}], subagent_thread_id="sub::t::c")
        assert ok is True
        assert result.status is SubagentStatus.INTERRUPTED
        assert result.interrupts == [{"value": "q", "id": "i1"}]
        assert result.subagent_thread_id == "sub::t::c"
        assert result.interrupted_at is not None
        # completed_at must stay None so cleanup heuristics do not treat it as finished.
        assert result.completed_at is None

    def test_try_set_interrupted_one_shot(self, _setup_executor_classes):
        classes = _setup_executor_classes
        result = _make_result(classes)
        assert result.try_set_interrupted(interrupts=[{"value": "q1"}]) is True
        assert result.try_set_interrupted(interrupts=[{"value": "q2"}]) is False
        assert result.interrupts == [{"value": "q1"}]

    def test_try_set_interrupted_rejected_from_terminal(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        result = _make_result(classes, SubagentStatus.COMPLETED)
        assert result.try_set_interrupted(interrupts=[{"value": "q"}]) is False
        assert result.status is SubagentStatus.COMPLETED

    def test_try_resume_from_interrupted(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        result = _make_result(classes)
        result.try_set_interrupted(interrupts=[{"value": "q"}], subagent_thread_id="sub::t::c")
        result.cancel_event.set()
        assert result.try_resume() is True
        assert result.status is SubagentStatus.RUNNING
        assert result.interrupted_at is None
        assert result.interrupts is None
        assert result.cancel_event.is_set() is False

    def test_try_resume_rejected_unless_interrupted(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        assert _make_result(classes).try_resume() is False
        assert _make_result(classes, SubagentStatus.COMPLETED).try_resume() is False

    def test_try_set_terminal_still_rejects_interrupted_status(self, _setup_executor_classes):
        classes = _setup_executor_classes
        result = _make_result(classes)
        with pytest.raises(ValueError):
            result.try_set_terminal(classes["SubagentStatus"].INTERRUPTED)


class TestCleanupKeepsInterrupted:
    def test_cleanup_does_not_remove_interrupted(self, _setup_executor_classes):
        classes = _setup_executor_classes
        SubagentStatus = classes["SubagentStatus"]
        executor_module = classes["executor_module"]
        result = _make_result(classes)
        result.try_set_interrupted(interrupts=[{"value": "q"}], subagent_thread_id="sub::t::c")

        executor_module._background_tasks["t1"] = result
        executor_module._subagent_executors["t1"] = MagicMock()

        executor_module.cleanup_background_task("t1")

        # INTERRUPTED must stay resident for resume.
        assert "t1" in executor_module._background_tasks
        assert "t1" in executor_module._subagent_executors

        # Cleanup after a real terminal transition removes both.
        result.try_set_terminal(SubagentStatus.COMPLETED, result="done")
        executor_module.cleanup_background_task("t1")
        assert "t1" not in executor_module._background_tasks
        assert "t1" not in executor_module._subagent_executors
