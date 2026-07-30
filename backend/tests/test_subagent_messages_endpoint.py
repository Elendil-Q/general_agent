"""Tests for GET /api/threads/{thread_id}/subagents/{task_id}/messages endpoint."""

from __future__ import annotations

import asyncio

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from app.gateway.routers import thread_runs
from deerflow.runtime.events.store.memory import MemoryRunEventStore

THREAD_ID = "thread-1"
TASK_ID = "task-a"


def _make_app(event_store=None, checkpointer=None):
    """Build a test FastAPI app with stub auth; event store / checkpointer are opt-in."""
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    if event_store is not None:
        app.state.run_event_store = event_store
    if checkpointer is not None:
        app.state.checkpointer = checkpointer
    return app


def _put(store, *, thread_id, run_id="run-1", category="subagent_message", content, metadata=None):
    return asyncio.run(
        store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type="llm.ai.response",
            category=category,
            content=content,
            metadata=metadata,
        )
    )


def _put_subagent_event(store, *, thread_id, task_id, content, subagent_type="general-purpose", description=""):
    return _put(
        store,
        thread_id=thread_id,
        category="subagent_message",
        content=content,
        metadata={"task_id": task_id, "subagent_type": subagent_type, "description": description},
    )


def _seed_mirror(checkpointer, thread_id, task_id, *, status="completed", subagent_type="general-purpose"):
    async def _seed():
        ckpt = empty_checkpoint()
        ckpt["channel_values"]["subagents"] = {
            task_id: {
                "task_id": task_id,
                "subagent_type": subagent_type,
                "status": status,
                "idle_expires_at": None,
                "updated_at": "2026-07-31T00:00:00+00:00",
            }
        }
        # InMemorySaver reconstructs channel_values on read from
        # checkpoint["channel_versions"] keys, so both must name the channel.
        ckpt["channel_versions"]["subagents"] = "1"
        await checkpointer.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            ckpt,
            {"step": 1, "source": "update", "writes": None, "parents": {}},
            {"subagents": "1"},
        )

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_only_matching_task_events_in_seq_order():
    """Only subagent_message events for the requested task_id are returned, seq ascending."""
    store = MemoryRunEventStore()
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "ai", "content": "first"}, description="research x")
    _put_subagent_event(store, thread_id=THREAD_ID, task_id="task-b", content={"type": "ai", "content": "other task"})
    _put(store, thread_id=THREAD_ID, category="message", content={"type": "human", "content": "lead message"})
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "tool", "content": "second"})

    app = _make_app(event_store=store, checkpointer=InMemorySaver())
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == TASK_ID
    assert body["thread_id"] == THREAD_ID
    messages = body["messages"]
    assert len(messages) == 2
    assert [m["seq"] for m in messages] == sorted(m["seq"] for m in messages)
    assert all(m["category"] == "subagent_message" for m in messages)
    assert all(m["metadata"]["task_id"] == TASK_ID for m in messages)
    assert messages[0]["content"] == {"type": "ai", "content": "first"}
    assert messages[1]["content"] == {"type": "tool", "content": "second"}


def test_subagent_type_and_description_come_from_first_event_metadata():
    store = MemoryRunEventStore()
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "ai", "content": "hi"}, subagent_type="analyst", description="dig into data")

    app = _make_app(event_store=store, checkpointer=InMemorySaver())
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["subagent_type"] == "analyst"
    assert body["description"] == "dig into data"


# ---------------------------------------------------------------------------
# Mirror status / fallback behavior
# ---------------------------------------------------------------------------


def test_status_passthrough_when_mirror_entry_present():
    store = MemoryRunEventStore()
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "ai", "content": "hi"}, description="d")
    checkpointer = InMemorySaver()
    _seed_mirror(checkpointer, THREAD_ID, TASK_ID, status="completed")

    app = _make_app(event_store=store, checkpointer=checkpointer)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_status_none_when_events_exist_but_no_checkpoint():
    """No checkpoint (aget_tuple returns None) must not 500; status degrades to None."""
    store = MemoryRunEventStore()
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "ai", "content": "hi"}, description="d")

    app = _make_app(event_store=store, checkpointer=InMemorySaver())
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is None
    assert len(body["messages"]) == 1


def test_status_none_when_checkpointer_not_configured():
    """No checkpointer on app.state at all (e.g. memory backend) still returns 200."""
    store = MemoryRunEventStore()
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "ai", "content": "hi"}, description="d")

    app = _make_app(event_store=store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    assert response.json()["status"] is None


def test_mirror_fallback_when_no_persisted_events():
    """No events but a mirror entry: 200 with empty messages and mirror-derived fields."""
    store = MemoryRunEventStore()
    checkpointer = InMemorySaver()
    _seed_mirror(checkpointer, THREAD_ID, TASK_ID, status="running", subagent_type="researcher")

    app = _make_app(event_store=store, checkpointer=checkpointer)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == []
    assert body["subagent_type"] == "researcher"
    assert body["description"] == ""
    assert body["status"] == "running"


# ---------------------------------------------------------------------------
# Errors / defensive boundaries
# ---------------------------------------------------------------------------


def test_404_when_no_events_and_no_mirror_entry():
    store = MemoryRunEventStore()
    app = _make_app(event_store=store, checkpointer=InMemorySaver())
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 404
    assert response.json()["detail"] == f"Subagent task {TASK_ID} not found for thread {THREAD_ID}"


def test_checkpointer_failure_does_not_500():
    """A checkpointer whose aget_tuple raises degrades gracefully (events still returned)."""
    store = MemoryRunEventStore()
    _put_subagent_event(store, thread_id=THREAD_ID, task_id=TASK_ID, content={"type": "ai", "content": "hi"}, description="d")

    class _BrokenCheckpointer:
        async def aget_tuple(self, config):
            raise RuntimeError("checkpoint backend down")

    app = _make_app(event_store=store, checkpointer=_BrokenCheckpointer())
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is None
    assert len(body["messages"]) == 1


def test_503_when_event_store_not_configured():
    app = _make_app()
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/messages")

    assert response.status_code == 503
