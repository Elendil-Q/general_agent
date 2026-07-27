# backend/tests/test_budget_monitor.py
"""Tests for the BudgetMonitor soft/hard request budget."""

from __future__ import annotations

import math

from deerflow.subagents.budget import BudgetMonitor


def test_none_budget_never_triggers():
    m = BudgetMonitor(task_id="t1", thread_id="T1", soft=None)
    for _ in range(1000):
        m.tick()
    assert m.at_soft_limit() is False
    assert m.at_hard_limit() is False


def test_soft_limit_triggers_at_threshold():
    m = BudgetMonitor(task_id="t1", thread_id="T1", soft=200)
    for _ in range(199):
        m.tick()
    assert m.at_soft_limit() is False
    m.tick()  # 200
    assert m.at_soft_limit() is True


def test_hard_limit_is_ceil_of_1_5x():
    m = BudgetMonitor(task_id="t1", thread_id="T1", soft=200)
    hard = math.ceil(200 * 1.5)  # 300
    for _ in range(hard - 1):
        m.tick()
    assert m.at_hard_limit() is False
    m.tick()  # hard
    assert m.at_hard_limit() is True


def test_soft_only_fires_once():
    m = BudgetMonitor(task_id="t1", thread_id="T1", soft=10)
    events: list[dict] = []
    from deerflow.subagents.event_bus import event_bus

    unsub = event_bus.on("subagent:budget", events.append)
    try:
        for _ in range(15):
            m.tick()
        warnings = [e for e in events if e.get("event") == "warning"]
        assert len(warnings) == 1  # only the first crossing
        # The frontend's budget_warning/exceeded handler requires a human-readable
        # ``message`` field; without it the event is silently dropped from SSE.
        assert "message" in warnings[0], "budget warning event must include a message field"
        assert isinstance(warnings[0]["message"], str)
        exceeded = [e for e in events if e.get("event") == "exceeded"]
        assert exceeded, "expected an exceeded event at 15 > hard(15)"
        assert "message" in exceeded[0], "budget exceeded event must include a message field"
        assert isinstance(exceeded[0]["message"], str)
    finally:
        unsub()


def test_zero_budget_immediate_termination():
    m = BudgetMonitor(task_id="t1", thread_id="T1", soft=0)
    assert m.at_hard_limit() is True  # 0 requests already at hard (ceil(0)=0)
