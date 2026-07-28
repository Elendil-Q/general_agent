# backend/tests/test_pending_task_guard_middleware.py
"""Tests for the pending task guard middleware."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares import pending_task_guard_middleware as mod
from deerflow.agents.middlewares.pending_task_guard_middleware import (
    PendingTaskGuardMiddleware,
)

# Sentinel status objects - the real SubagentStatus is mocked by conftest.
_STATUS_PENDING = ("pending",)
_STATUS_RUNNING = ("running",)
_STATUS_COMPLETED = ("completed",)
_STATUS_IDLE = ("idle",)
_STATUS_INTERRUPTED = ("interrupted",)

_INFLIGHT = {_STATUS_PENDING, _STATUS_RUNNING}


def _make_runtime(thread_id: str = "test-thread") -> MagicMock:
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id}
    return runtime


def _state_with_ai_no_tools():
    ai = AIMessage(content="I'm done", tool_calls=[])
    return {"messages": [HumanMessage(content="do X"), ai]}


def _state_with_ai_with_tools():
    ai = AIMessage(content="calling tool", tool_calls=[{"name": "read_file", "args": {}, "id": "c1"}])
    return {"messages": [HumanMessage(content="do X"), ai]}


def _make_ref(task_id: str, thread_id: str, status):
    return SimpleNamespace(
        task_id=task_id,
        thread_id=thread_id,
        trace_id="trace-1",
        subagent_type="general-purpose",
        status=status,
        config=SimpleNamespace(timeout_seconds=300),
        executor=None,
        result=None,
        description="test task",
        created_at=datetime.now(),
        idle_since=None,
    )


def _setup_inflight():
    """Patch the module-level inflight set so it matches our sentinel statuses."""
    mod._INFLIGHT_STATUSES = _INFLIGHT


def test_no_reminder_when_no_pending_tasks():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.return_value = []
        out = m.after_model(_state_with_ai_no_tools(), _make_runtime())
    assert out is None


def test_reminder_injected_when_pending_tasks_exist():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    ref = _make_ref("t1", "test-thread", _STATUS_RUNNING)
    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.return_value = [ref]
        out = m.after_model(_state_with_ai_no_tools(), _make_runtime())
    assert out is not None
    injected = out["messages"][-1]
    assert "wait_for_tasks" in str(injected.content).lower()
    assert "t1" in str(injected.content)


def test_no_reminder_when_ai_has_tool_calls():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    ref = _make_ref("t1", "test-thread", _STATUS_RUNNING)
    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.return_value = [ref]
        out = m.after_model(_state_with_ai_with_tools(), _make_runtime())
    assert out is None


def test_no_reminder_when_tasks_from_different_thread():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    ref = _make_ref("t1", "other-thread", _STATUS_RUNNING)

    def _list_by_thread(tid):
        return [ref] if tid == "other-thread" else []

    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.side_effect = _list_by_thread
        out = m.after_model(_state_with_ai_no_tools(), _make_runtime("test-thread"))
    assert out is None


def test_no_reminder_when_tasks_are_terminal_or_stopped():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    refs = [
        _make_ref("t1", "test-thread", _STATUS_COMPLETED),
        _make_ref("t2", "test-thread", _STATUS_IDLE),
        _make_ref("t3", "test-thread", _STATUS_INTERRUPTED),
    ]
    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.return_value = refs
        out = m.after_model(_state_with_ai_no_tools(), _make_runtime())
    assert out is None


def test_third_reminder_forces_tool_choice():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    ref = _make_ref("t1", "test-thread", _STATUS_RUNNING)
    state = _state_with_ai_no_tools()
    runtime = _make_runtime()
    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.return_value = [ref]
        m.after_model(state, runtime)  # 1
        m.after_model(state, runtime)  # 2
        out = m.after_model(state, runtime)  # 3
    assert out is not None
    assert out["tool_choice"] == {"type": "tool", "name": "wait_for_tasks"}


def test_no_thread_id_returns_none():
    m = PendingTaskGuardMiddleware()
    runtime = MagicMock()
    runtime.context = {}
    out = m.after_model(_state_with_ai_no_tools(), runtime)
    assert out is None


def test_empty_messages_returns_none():
    m = PendingTaskGuardMiddleware()
    out = m.after_model({"messages": []}, _make_runtime())
    assert out is None


def test_last_message_not_ai_returns_none():
    m = PendingTaskGuardMiddleware()
    state = {"messages": [HumanMessage(content="hello")]}
    out = m.after_model(state, _make_runtime())
    assert out is None


def test_multiple_pending_tasks_all_listed():
    _setup_inflight()
    m = PendingTaskGuardMiddleware()
    refs = [
        _make_ref("t1", "test-thread", _STATUS_PENDING),
        _make_ref("t2", "test-thread", _STATUS_RUNNING),
    ]
    with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
        mock_reg.list_by_thread.return_value = refs
        out = m.after_model(_state_with_ai_no_tools(), _make_runtime())
    assert out is not None
    content = str(out["messages"][-1].content)
    assert "t1" in content
    assert "t2" in content
    assert "2" in content
