"""Tests for the process-level EventBus pub/sub."""

from __future__ import annotations

from deerflow.subagents.event_bus import EventBus


def test_emit_delivers_to_subscriber():
    bus = EventBus()
    received: list[dict] = []
    bus.on("subagent:lifecycle", received.append)
    bus.emit("subagent:lifecycle", {"event": "started", "task_id": "t1"})
    assert received == [{"event": "started", "task_id": "t1"}]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received: list[dict] = []
    unsub = bus.on("subagent:lifecycle", received.append)
    bus.emit("subagent:lifecycle", {"event": "started"})
    unsub()
    bus.emit("subagent:lifecycle", {"event": "completed"})
    assert received == [{"event": "started"}]


def test_channel_isolation():
    bus = EventBus()
    life: list[dict] = []
    prog: list[dict] = []
    bus.on("subagent:lifecycle", life.append)
    bus.on("subagent:progress", prog.append)
    bus.emit("subagent:lifecycle", {"e": "a"})
    bus.emit("subagent:progress", {"e": "b"})
    assert life == [{"e": "a"}]
    assert prog == [{"e": "b"}]


def test_multiple_subscribers_independent():
    bus = EventBus()
    a: list[dict] = []
    b: list[dict] = []
    bus.on("subagent:lifecycle", a.append)
    bus.on("subagent:lifecycle", b.append)
    bus.emit("subagent:lifecycle", {"e": "x"})
    assert a == [{"e": "x"}]
    assert b == [{"e": "x"}]


def test_handler_exception_does_not_poison_others():
    bus = EventBus()
    good: list[dict] = []

    def bad(_payload: dict) -> None:
        raise RuntimeError("boom")

    bus.on("subagent:lifecycle", bad)
    bus.on("subagent:lifecycle", good.append)
    bus.emit("subagent:lifecycle", {"e": "y"})
    assert good == [{"e": "y"}]
