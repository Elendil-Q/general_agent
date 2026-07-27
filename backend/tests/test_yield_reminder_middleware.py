# backend/tests/test_yield_reminder_middleware.py
"""Tests for the yield reminder middleware."""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.yield_reminder_middleware import (
    YieldReminderMiddleware,
)


def _make_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.context = {"thread_id": "test-thread"}
    return runtime


def _state_with_ai(tool_calls: list[dict] | None = None):
    ai = AIMessage(content="working", tool_calls=tool_calls or [])
    return {"messages": [HumanMessage(content="do X"), ai]}


def test_no_reminder_when_yield_called():
    m = YieldReminderMiddleware()
    state = _state_with_ai([{"name": "yield", "args": {}, "id": "c1"}])
    out = m.after_model(state, _make_runtime())
    assert out is None  # already yielding, no reminder injected


def test_reminder_injected_when_no_yield():
    m = YieldReminderMiddleware()
    state = _state_with_ai([{"name": "read_file", "args": {}, "id": "c1"}])
    out = m.after_model(state, _make_runtime())
    assert out is not None
    injected = out["messages"][-1]
    assert "yield" in str(injected.content).lower()


def test_third_reminder_forces_tool_choice():
    m = YieldReminderMiddleware()
    state = _state_with_ai([{"name": "read_file", "args": {}, "id": "c1"}])
    runtime = _make_runtime()
    m.after_model(state, runtime)  # 1
    m.after_model(state, runtime)  # 2
    out = m.after_model(state, runtime)  # 3
    assert out is not None
    assert out["tool_choice"] == {"type": "tool", "name": "yield"}
