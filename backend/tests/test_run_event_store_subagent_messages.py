"""Tests for RunEventStore.list_subagent_messages across all backends.

Covers the ``category="subagent_message"`` query added for the subagent
conversation view: filtering on category + metadata["task_id"], seq-ascending
ordering, and the guarantee that subagent_message events stay out of the
regular ``list_messages()`` projection.
"""

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore


@pytest.fixture(params=["memory", "jsonl", "db"])
async def store(request, tmp_path):
    if request.param == "memory":
        yield MemoryRunEventStore()
    elif request.param == "jsonl":
        from deerflow.runtime.events.store.jsonl import JsonlRunEventStore

        yield JsonlRunEventStore(base_dir=tmp_path / "jsonl")
    else:
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
        from deerflow.runtime.events.store.db import DbRunEventStore

        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
        try:
            yield DbRunEventStore(get_session_factory())
        finally:
            await close_engine()


async def _seed_mixed_events(store):
    """Two subagent_message events for task-1, one for task-2, plus a normal message.

    Write order interleaves the two task_ids so the returned ordering can only
    be correct if results are sorted by seq.
    """
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="ai_message",
        category="subagent_message",
        content="task-1 first",
        metadata={"task_id": "task-1", "subagent_type": "general-purpose", "description": "research"},
    )
    await store.put(thread_id="t1", run_id="r1", event_type="human_message", category="message", content="normal user message")
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="ai_message",
        category="subagent_message",
        content="task-2 only",
        metadata={"task_id": "task-2", "subagent_type": "general-purpose", "description": "summarize"},
    )
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="ai_message",
        category="subagent_message",
        content="task-1 second",
        metadata={"task_id": "task-1", "subagent_type": "general-purpose", "description": "research"},
    )


class TestListSubagentMessages:
    @pytest.mark.anyio
    async def test_filters_by_category_and_task_id_ordered_by_seq(self, store):
        await _seed_mixed_events(store)

        msgs = await store.list_subagent_messages("t1", "task-1")

        assert [m["content"] for m in msgs] == ["task-1 first", "task-1 second"]
        assert all(m["category"] == "subagent_message" for m in msgs)
        assert all(m["metadata"]["task_id"] == "task-1" for m in msgs)
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs)

    @pytest.mark.anyio
    async def test_other_task_id_returns_only_its_own_messages(self, store):
        await _seed_mixed_events(store)

        msgs = await store.list_subagent_messages("t1", "task-2")

        assert [m["content"] for m in msgs] == ["task-2 only"]

    @pytest.mark.anyio
    async def test_returns_empty_list_when_no_match(self, store):
        await _seed_mixed_events(store)

        assert await store.list_subagent_messages("t1", "task-nope") == []

    @pytest.mark.anyio
    async def test_returns_empty_list_for_unknown_thread(self, store):
        assert await store.list_subagent_messages("nope", "task-1") == []

    @pytest.mark.anyio
    async def test_events_missing_task_id_metadata_are_excluded(self, store):
        await store.put(thread_id="t1", run_id="r1", event_type="ai_message", category="subagent_message", content="no task_id")
        await store.put(thread_id="t1", run_id="r1", event_type="ai_message", category="subagent_message", content="empty metadata", metadata=None)

        assert await store.list_subagent_messages("t1", "task-1") == []

    @pytest.mark.anyio
    async def test_thread_isolation(self, store):
        await store.put(
            thread_id="t2",
            run_id="r1",
            event_type="ai_message",
            category="subagent_message",
            content="other thread",
            metadata={"task_id": "task-1"},
        )

        assert await store.list_subagent_messages("t1", "task-1") == []

    @pytest.mark.anyio
    async def test_list_messages_excludes_subagent_message_events(self, store):
        await _seed_mixed_events(store)

        messages = await store.list_messages("t1")

        assert [m["content"] for m in messages] == ["normal user message"]
        assert all(m["category"] == "message" for m in messages)
