"""Bridges EventBus payloads to per-lead-run LangGraph stream writers.

``get_stream_writer()`` is scoped to one lead run; the EventBus is
process-global. This bridge holds a ``thread_id -> writer`` map: lead-run
tools (``task(detached=False)`` / ``wait_for_tasks`` / ``follow_up``)
register their writer on entry and unregister on exit. Events with no
registered writer are dropped from SSE (their state is already in the
Registry, so no result is lost).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock

from deerflow.subagents.event_bus import event_bus

logger = logging.getLogger(__name__)
Writer = Callable[[dict], None]

# EventBus event name -> SSE ``type`` string.
_LIFECYCLE_TYPE = {
    "started": "task_started",
    "running": "task_running",
    "completed": "task_completed",
    "failed": "task_failed",
    "cancelled": "task_cancelled",
    "timed_out": "task_timed_out",
    "interrupted": "task_interrupted",
    "idle": "task_idle",
    "revived": "task_revived",
    "expired": "task_expired",
}
_BUDGET_TYPE = {"warning": "budget_warning", "exceeded": "budget_exceeded"}


class SSEBridge:
    def __init__(self) -> None:
        self._writers: dict[str, Writer] = {}
        self._lock = Lock()
        # subscribe once to all channels
        event_bus.on("subagent:lifecycle", self._on_lifecycle)
        event_bus.on("subagent:progress", self._on_progress)
        event_bus.on("subagent:budget", self._on_budget)

    def register_writer(self, thread_id: str, writer: Writer) -> None:
        with self._lock:
            self._writers[thread_id] = writer

    def unregister_writer(self, thread_id: str) -> None:
        with self._lock:
            self._writers.pop(thread_id, None)

    def _dispatch(self, payload: dict, type_key: str) -> None:
        thread_id = payload.get("thread_id")
        if thread_id is None:
            return
        with self._lock:
            writer = self._writers.get(thread_id)
        if writer is None:
            return
        event = {"type": type_key, **payload}
        try:
            writer(event)
        except Exception:
            logger.exception("SSE writer raised for thread %s", thread_id)

    def _on_lifecycle(self, payload: dict) -> None:
        self._dispatch(payload, _LIFECYCLE_TYPE.get(payload.get("event", ""), "task_event"))

    def _on_progress(self, payload: dict) -> None:
        self._dispatch(payload, "task_progress")

    def _on_budget(self, payload: dict) -> None:
        self._dispatch(payload, _BUDGET_TYPE.get(payload.get("event", ""), "budget_event"))


sse_bridge = SSEBridge()
