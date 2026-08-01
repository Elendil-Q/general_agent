"""Builds the persisted subagent status mirror (ThreadState.subagents).

The AgentRegistry is the runtime authority (in-memory, process-local). This
module turns a registry snapshot into a checkpointable mirror so the frontend
can reconstruct true subagent status on cold open (thread switch / gateway
restart) and derive IDLE TTL expiry locally via ``idle_expires_at``.

Reconcile rules for entries present in the mirror but gone from the registry
(TTL reaped or process restart):
- last status ``idle``        -> ``completed`` (mirrors the task_expired SSE semantics)
- ``running``/``pending``/``interrupted`` -> ``cancelled`` (terminated by
  lifecycle, not by the task itself; the mirror is a UI-facing channel, so the
  existing ``cancelled`` value doubles for "vanished mid-flight")
Terminal mirror entries (completed/failed/cancelled) are preserved as history.
"""

from __future__ import annotations

from collections.abc import Iterable

from deerflow.subagents.executor import SubagentStatus

MIRROR_STATUS_CANCELLED = "cancelled"

_STATUS_MAP = {
    SubagentStatus.PENDING: "pending",
    SubagentStatus.RUNNING: "running",
    SubagentStatus.IDLE: "idle",
    SubagentStatus.INTERRUPTED: "interrupted",
    SubagentStatus.COMPLETED: "completed",
    SubagentStatus.FAILED: "failed",
    SubagentStatus.CANCELLED: MIRROR_STATUS_CANCELLED,
    SubagentStatus.TIMED_OUT: "failed",
}

_TERMINAL_MIRROR_STATUSES = {"completed", "failed", MIRROR_STATUS_CANCELLED}


def _entry_from_ref(ref, now: float, ttl_seconds: float) -> dict:
    status = _STATUS_MAP[ref.status]
    idle_expires_at = None
    if status == "idle" and getattr(ref, "idle_since", None) is not None:
        idle_expires_at = ref.idle_since.timestamp() + ttl_seconds
    return {
        "task_id": ref.task_id,
        "subagent_type": ref.subagent_type,
        "status": status,
        "idle_expires_at": idle_expires_at,
        "updated_at": now,
        "parent_task_id": getattr(ref, "parent_task_id", None),
    }


def _strip_updated_at(snapshot: dict[str, dict]) -> dict[str, dict]:
    return {task_id: {k: v for k, v in entry.items() if k != "updated_at"} for task_id, entry in snapshot.items()}


def build_subagents_snapshot(
    refs: Iterable,
    existing: dict[str, dict] | None,
    *,
    now: float,
    ttl_seconds: float,
) -> dict[str, dict] | None:
    """Compute the next mirror snapshot from registry refs + the persisted mirror.

    Returns None when nothing changed (callers must not write), else the full
    replacement snapshot. ``updated_at`` is bumped only on entries whose
    meaningful fields changed.
    """
    existing = existing or {}
    ref_ids = {ref.task_id for ref in refs}

    snapshot: dict[str, dict] = {}
    for ref in refs:
        snapshot[ref.task_id] = _entry_from_ref(ref, now, ttl_seconds)

    for task_id, entry in existing.items():
        if task_id in ref_ids:
            continue
        if entry.get("status") in _TERMINAL_MIRROR_STATUSES:
            snapshot[task_id] = dict(entry)
            continue
        flipped = dict(entry)
        flipped["status"] = "completed" if entry.get("status") == "idle" else MIRROR_STATUS_CANCELLED
        flipped["idle_expires_at"] = None
        flipped["updated_at"] = now
        snapshot[task_id] = flipped

    if not snapshot:
        return None
    if _strip_updated_at(snapshot) == _strip_updated_at(existing):
        return None
    for task_id, entry in snapshot.items():
        old = existing.get(task_id)
        if old is not None and _strip_updated_at({task_id: entry}) == _strip_updated_at({task_id: old}):
            entry["updated_at"] = old.get("updated_at", now)
    return snapshot
