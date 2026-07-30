"""Tests for SubagentMessagePersister (event-bus -> run event store bridge).

The subagent executor emits ``subagent:message`` events on its isolated
event-loop thread; the persister captures the Gateway main loop at bind time
and schedules ``store.put`` back onto it via ``asyncio.run_coroutine_threadsafe``.
"""

import asyncio
from datetime import datetime

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.subagents.event_bus import event_bus
from deerflow.subagents.message_persistence import SubagentMessagePersister


def _payload(**overrides):
    payload = {
        "task_id": "task-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "subagent_type": "general-purpose",
        "message": {"id": "msg-1", "type": "ai", "content": "hello"},
    }
    payload.update(overrides)
    return payload


async def _wait_for_messages(store, thread_id, task_id, *, expected=1, timeout=2.0):
    """Poll the store until ``expected`` messages land (or timeout)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        msgs = await store.list_subagent_messages(thread_id, task_id)
        if len(msgs) >= expected or loop.time() > deadline:
            return msgs
        await asyncio.sleep(0.01)


class TestSubagentMessagePersister:
    @pytest.mark.anyio
    async def test_persists_emitted_message(self):
        store = MemoryRunEventStore()
        persister = SubagentMessagePersister()
        unsub = persister.bind(loop=asyncio.get_running_loop(), event_store=store)
        try:
            event_bus.emit("subagent:message", _payload())
            msgs = await _wait_for_messages(store, "thread-1", "task-1")
        finally:
            unsub()

        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["thread_id"] == "thread-1"
        assert msg["run_id"] == "run-1"
        assert msg["event_type"] == "subagent.message"
        assert msg["category"] == "subagent_message"
        assert msg["content"] == {"id": "msg-1", "type": "ai", "content": "hello"}
        assert msg["metadata"] == {
            "task_id": "task-1",
            "subagent_type": "general-purpose",
            "description": "",
        }

    @pytest.mark.anyio
    async def test_description_resolved_from_agent_registry(self):
        from deerflow.subagents.agent_registry import AgentRef, agent_registry

        agent_registry.register(
            AgentRef(
                task_id="task-reg",
                thread_id="thread-1",
                trace_id="trace-reg",
                subagent_type="general-purpose",
                status=None,
                config=None,
                executor=None,
                result=None,
                description="research the topic",
                created_at=datetime.now(),
            )
        )
        store = MemoryRunEventStore()
        persister = SubagentMessagePersister()
        unsub = persister.bind(loop=asyncio.get_running_loop(), event_store=store)
        try:
            event_bus.emit("subagent:message", _payload(task_id="task-reg"))
            msgs = await _wait_for_messages(store, "thread-1", "task-reg")
        finally:
            unsub()
            agent_registry.remove("task-reg")

        assert len(msgs) == 1
        assert msgs[0]["metadata"]["description"] == "research the topic"

    @pytest.mark.anyio
    async def test_empty_run_id_falls_back_to_subagent(self):
        store = MemoryRunEventStore()
        persister = SubagentMessagePersister()
        unsub = persister.bind(loop=asyncio.get_running_loop(), event_store=store)
        try:
            event_bus.emit("subagent:message", _payload(run_id=""))
            msgs = await _wait_for_messages(store, "thread-1", "task-1")
        finally:
            unsub()

        assert len(msgs) == 1
        assert msgs[0]["run_id"] == "subagent"

    @pytest.mark.anyio
    async def test_unsubscribe_stops_persistence(self):
        store = MemoryRunEventStore()
        persister = SubagentMessagePersister()
        unsub = persister.bind(loop=asyncio.get_running_loop(), event_store=store)
        unsub()

        event_bus.emit("subagent:message", _payload())
        await asyncio.sleep(0.1)

        assert await store.list_subagent_messages("thread-1", "task-1") == []

    @pytest.mark.anyio
    async def test_on_message_before_bind_is_dropped_without_error(self):
        persister = SubagentMessagePersister()
        # No bind(): must not raise, must log-and-drop.
        persister._on_message(_payload())
        await asyncio.sleep(0)

    @pytest.mark.anyio
    async def test_non_dict_message_payload_is_skipped_with_warning(self, caplog):
        """A malformed ``subagent:message`` payload whose ``message`` is not a
        dict must be dropped (with a warning) instead of persisted as a raw
        string — consumers expect a message dict."""
        store = MemoryRunEventStore()
        persister = SubagentMessagePersister()
        unsub = persister.bind(loop=asyncio.get_running_loop(), event_store=store)
        try:
            with caplog.at_level("WARNING"):
                event_bus.emit("subagent:message", _payload(message="not-a-dict"))
            await asyncio.sleep(0.1)
        finally:
            unsub()

        assert await store.list_subagent_messages("thread-1", "task-1") == []
        assert any("message" in record.message and "dict" in record.message for record in caplog.records)
