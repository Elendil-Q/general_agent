"""Tests for the subagent state mirror (ThreadState.subagents channel).

The mirror is a persisted snapshot of the in-memory AgentRegistry, synced by
SubagentContextMiddleware.abefore_model. It lets the frontend reconstruct true
subagent status on cold open (thread switch / gateway restart) and derive IDLE
TTL expiry locally via idle_expires_at.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from deerflow.agents.middlewares.subagent_context_middleware import SubagentContextMiddleware
from deerflow.agents.thread_state import ThreadState, merge_subagents
from deerflow.subagents.executor import SubagentStatus
from deerflow.subagents.state_mirror import (
    MIRROR_STATUS_CANCELLED,
    build_subagents_snapshot,
)

NOW = 1_800_000_000.0
TTL = 420.0


def _make_ref(
    task_id: str = "t1",
    status: SubagentStatus = SubagentStatus.RUNNING,
    subagent_type: str = "general-purpose",
    idle_since: datetime | None = None,
):
    return SimpleNamespace(
        task_id=task_id,
        thread_id="test-thread",
        subagent_type=subagent_type,
        status=status,
        idle_since=idle_since,
    )


# ---------------------------------------------------------------------------
# merge_subagents reducer
# ---------------------------------------------------------------------------


class TestMergeSubagents:
    def test_new_none_preserves_existing(self):
        existing = {"t1": {"task_id": "t1", "status": "running"}}
        assert merge_subagents(existing, None) is existing

    def test_new_value_replaces_existing(self):
        existing = {"t1": {"task_id": "t1", "status": "running"}}
        new = {"t1": {"task_id": "t1", "status": "completed"}}
        assert merge_subagents(existing, new) == new

    def test_empty_dict_is_explicit_update(self):
        existing = {"t1": {"task_id": "t1", "status": "running"}}
        assert merge_subagents(existing, {}) == {}


class TestThreadStateChannel:
    def test_thread_state_declares_subagents_channel(self):
        assert "subagents" in ThreadState.__annotations__

    def test_middleware_state_schema_declares_subagents_channel(self):
        assert "subagents" in SubagentContextMiddleware.state_schema.__annotations__


# ---------------------------------------------------------------------------
# build_subagents_snapshot: registry refs -> mirror entries
# ---------------------------------------------------------------------------


class TestSnapshotFromRefs:
    def test_running_ref_mirrored(self):
        snapshot = build_subagents_snapshot([_make_ref("t1")], None, now=NOW, ttl_seconds=TTL)
        assert snapshot is not None
        entry = snapshot["t1"]
        assert entry["task_id"] == "t1"
        assert entry["subagent_type"] == "general-purpose"
        assert entry["status"] == "running"
        assert entry["updated_at"] == NOW
        assert entry.get("idle_expires_at") is None

    def test_idle_ref_gets_idle_expires_at(self):
        idle_since = datetime.fromtimestamp(NOW - 60, tz=UTC)
        snapshot = build_subagents_snapshot([_make_ref("t1", status=SubagentStatus.IDLE, idle_since=idle_since)], None, now=NOW, ttl_seconds=TTL)
        assert snapshot is not None
        entry = snapshot["t1"]
        assert entry["status"] == "idle"
        assert entry["idle_expires_at"] == pytest.approx(NOW - 60 + TTL)

    def test_cancelled_kept_and_timed_out_maps_to_failed(self):
        refs = [
            _make_ref("t1", status=SubagentStatus.CANCELLED),
            _make_ref("t2", status=SubagentStatus.TIMED_OUT),
        ]
        snapshot = build_subagents_snapshot(refs, None, now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == MIRROR_STATUS_CANCELLED
        assert snapshot["t2"]["status"] == "failed"

    def test_pending_and_interrupted_kept(self):
        refs = [
            _make_ref("t1", status=SubagentStatus.PENDING),
            _make_ref("t2", status=SubagentStatus.INTERRUPTED),
        ]
        snapshot = build_subagents_snapshot(refs, None, now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == "pending"
        assert snapshot["t2"]["status"] == "interrupted"


# ---------------------------------------------------------------------------
# build_subagents_snapshot: reconcile (state has, registry doesn't)
# ---------------------------------------------------------------------------


def _existing(status: str, task_id: str = "t1") -> dict:
    return {
        task_id: {
            "task_id": task_id,
            "subagent_type": "general-purpose",
            "status": status,
            "idle_expires_at": None,
            "updated_at": NOW - 100,
        }
    }


class TestReconcile:
    def test_vanished_idle_becomes_completed(self):
        snapshot = build_subagents_snapshot([], _existing("idle"), now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == "completed"
        assert snapshot["t1"]["updated_at"] == NOW

    def test_vanished_running_becomes_cancelled(self):
        snapshot = build_subagents_snapshot([], _existing("running"), now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == MIRROR_STATUS_CANCELLED

    def test_vanished_pending_becomes_cancelled(self):
        snapshot = build_subagents_snapshot([], _existing("pending"), now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == MIRROR_STATUS_CANCELLED

    def test_vanished_interrupted_becomes_cancelled(self):
        snapshot = build_subagents_snapshot([], _existing("interrupted"), now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == MIRROR_STATUS_CANCELLED

    def test_vanished_terminal_preserved(self):
        existing = _existing("completed")
        assert build_subagents_snapshot([], existing, now=NOW, ttl_seconds=TTL) is None

    def test_registry_entry_wins_over_stale_mirror(self):
        existing = _existing("idle")
        refs = [_make_ref("t1", status=SubagentStatus.RUNNING)]
        snapshot = build_subagents_snapshot(refs, existing, now=NOW, ttl_seconds=TTL)
        assert snapshot["t1"]["status"] == "running"

    def test_unchanged_returns_none(self):
        refs = [_make_ref("t1", status=SubagentStatus.RUNNING)]
        first = build_subagents_snapshot(refs, None, now=NOW, ttl_seconds=TTL)
        assert first is not None
        again = build_subagents_snapshot(refs, first, now=NOW + 1, ttl_seconds=TTL)
        assert again is None

    def test_empty_everything_returns_none(self):
        assert build_subagents_snapshot([], None, now=NOW, ttl_seconds=TTL) is None
        assert build_subagents_snapshot([], {}, now=NOW, ttl_seconds=TTL) is None


# ---------------------------------------------------------------------------
# Middleware abefore_model integration
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, thread_id: str | None):
        self.context = {"thread_id": thread_id} if thread_id else {}


def _register_ref(registry, **kwargs):
    ref = _make_ref(**kwargs)
    registry.register(ref)
    return ref


def _patch_registry(monkeypatch, registry) -> None:
    """Patch the registry singleton the middleware's lazy import will see.

    Must patch the sys.modules entry directly: other suites (e.g.
    test_subagent_registry_shim) re-import ``deerflow.subagents.agent_registry``
    fresh and leave a stale attribute on the ``deerflow.subagents`` package, so
    monkeypatch's dotted-string resolution (getattr chain from the top package)
    can land on the stale module while from-imports resolve via sys.modules.
    """
    monkeypatch.setattr(sys.modules["deerflow.subagents.agent_registry"], "agent_registry", registry)


class TestMiddlewareBeforeModel:
    @pytest.fixture
    def registry(self):
        from deerflow.subagents.agent_registry import AgentRegistry

        return AgentRegistry()

    @pytest.mark.asyncio
    async def test_writes_snapshot_when_registry_has_refs(self, registry, monkeypatch):
        _register_ref(registry, task_id="t1", status=SubagentStatus.RUNNING)
        _patch_registry(monkeypatch, registry)

        middleware = SubagentContextMiddleware()
        update = await middleware.abefore_model({"messages": []}, _FakeRuntime("test-thread"))
        assert update is not None
        assert update["subagents"]["t1"]["status"] == "running"

    @pytest.mark.asyncio
    async def test_returns_none_without_thread_id(self, registry, monkeypatch):
        _register_ref(registry, task_id="t1")
        _patch_registry(monkeypatch, registry)

        middleware = SubagentContextMiddleware()
        assert await middleware.abefore_model({"messages": []}, _FakeRuntime(None)) is None

    @pytest.mark.asyncio
    async def test_reconciles_orphans_on_restart(self, registry, monkeypatch):
        """Cold open after gateway restart: registry empty, state has non-terminal entries."""
        _patch_registry(monkeypatch, registry)

        middleware = SubagentContextMiddleware()
        state = {
            "messages": [],
            "subagents": {
                **_existing("idle", "t1"),
                **_existing("running", "t2"),
            },
        }
        update = await middleware.abefore_model(state, _FakeRuntime("test-thread"))
        assert update is not None
        assert update["subagents"]["t1"]["status"] == "completed"
        assert update["subagents"]["t2"]["status"] == MIRROR_STATUS_CANCELLED

    @pytest.mark.asyncio
    async def test_no_change_returns_none(self, registry, monkeypatch):
        _register_ref(registry, task_id="t1", status=SubagentStatus.RUNNING)
        _patch_registry(monkeypatch, registry)

        middleware = SubagentContextMiddleware()
        first = await middleware.abefore_model({"messages": []}, _FakeRuntime("test-thread"))
        assert first is not None
        again = await middleware.abefore_model({"messages": [], "subagents": first["subagents"]}, _FakeRuntime("test-thread"))
        assert again is None
