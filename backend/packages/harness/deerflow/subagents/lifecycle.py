"""IDLE subagent TTL adoption and cleanup.

When a subagent with ``keep_alive=True`` completes, it enters IDLE and is
adopted here: a TTL timer runs; if no ``follow_up`` revives it within the
TTL, it is cleaned up (removed from the Registry). In-memory only - no
disk parking/revival.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from deerflow.subagents.event_bus import event_bus

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 420  # 7 minutes, mirroring OMP's default


class SubagentLifecycleManager:
    """Manages TTL timers for IDLE subagents."""

    def __init__(self) -> None:
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._on_expire: Callable[[str], None] | None = None

    def set_expire_callback(self, cb: Callable[[str], None]) -> None:
        """Override the expiry handler (default: cleanup via the accessor)."""
        self._on_expire = cb

    def adopt(self, task_id: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self.cancel_ttl(task_id)  # replace any prior timer
        timer = threading.Timer(ttl_seconds, self._expire, args=(task_id,))
        timer.daemon = True
        with self._lock:
            self._timers[task_id] = timer
        timer.start()

    def cancel_ttl(self, task_id: str) -> None:
        with self._lock:
            timer = self._timers.pop(task_id, None)
        if timer is not None:
            timer.cancel()

    def dismiss(self, task_id: str) -> None:
        self.cancel_ttl(task_id)
        self._do_cleanup(task_id)

    def _expire(self, task_id: str) -> None:
        with self._lock:
            self._timers.pop(task_id, None)
        from deerflow.subagents.agent_registry import agent_registry

        ref = agent_registry.get(task_id)
        thread_id = ref.thread_id if ref is not None else ""
        event_bus.emit(
            "subagent:lifecycle",
            {"event": "expired", "task_id": task_id, "thread_id": thread_id},
        )
        self._do_cleanup(task_id)

    def _do_cleanup(self, task_id: str) -> None:
        if self._on_expire is not None:
            self._on_expire(task_id)
            return
        # default: mark terminal + remove via the accessor shim. Imports are
        # lazy to avoid a circular import with the executor module.
        from deerflow.subagents.agent_registry import agent_registry
        from deerflow.subagents.executor import SubagentStatus, cleanup_background_task

        ref = agent_registry.update_status(task_id, SubagentStatus.COMPLETED)
        thread_id = ref.thread_id if ref is not None else ""
        # cleanup_background_task only removes entries whose *result* is in a
        # terminal state; update_status mutates the AgentRef.status but not the
        # nested SubagentResult.status, so transition the result too. This is
        # idempotent: try_set_terminal is a no-op once already terminal.
        if ref is not None and ref.result is not None and not ref.result.status.is_terminal:
            ref.result.try_set_terminal(SubagentStatus.COMPLETED)
        cleanup_background_task(task_id)

        # Release the SSE writer now that this subagent is COMPLETED. The
        # writer was kept alive (see sse_bridge.unregister_writer) so the
        # expired event could reach the frontend. If no other non-terminal
        # subagents remain for this thread, the writer is finally released.
        if thread_id:
            from deerflow.subagents.sse_bridge import sse_bridge

            sse_bridge.unregister_writer(thread_id)


# Process-level singleton.
lifecycle_manager = SubagentLifecycleManager()
