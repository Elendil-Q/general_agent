"""Tests for the SSE bridge routing EventBus payloads to per-run writers."""

from __future__ import annotations

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
