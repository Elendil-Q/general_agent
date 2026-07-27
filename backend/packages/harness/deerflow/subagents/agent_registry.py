"""Process-level registry of all subagent AgentRefs.

Replaces the module-level ``_background_tasks`` / ``_subagent_executors``
dicts with a single state authority. Thread-safe via a Lock. Status
changes fire registered handlers (used by the SSE bridge).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.executor import SubagentExecutor, SubagentResult, SubagentStatus

ChangeHandler = Callable[["AgentRef"], None]


@dataclass
class AgentRef:
    task_id: str
    thread_id: str
    trace_id: str
    subagent_type: str
    status: SubagentStatus
    config: SubagentConfig
    executor: SubagentExecutor | None
    result: SubagentResult | None
    description: str
    created_at: datetime
    idle_since: datetime | None = None


class AgentRegistry:
    """Thread-safe store of AgentRefs keyed by task_id."""

    def __init__(self) -> None:
        self._refs: dict[str, AgentRef] = {}
        self._lock = Lock()
        self._change_handlers: list[ChangeHandler] = []

    def register(self, ref: AgentRef) -> None:
        with self._lock:
            self._refs[ref.task_id] = ref

    def get(self, task_id: str) -> AgentRef | None:
        with self._lock:
            return self._refs.get(task_id)

    def list_by_thread(self, thread_id: str) -> list[AgentRef]:
        with self._lock:
            return [r for r in self._refs.values() if r.thread_id == thread_id]

    def list_idle(self, thread_id: str | None = None) -> list[AgentRef]:
        with self._lock:
            return [r for r in self._refs.values() if r.status is SubagentStatus.IDLE and (thread_id is None or r.thread_id == thread_id)]

    def list_all(self) -> list[AgentRef]:
        with self._lock:
            return list(self._refs.values())

    def update_status(self, task_id: str, status: SubagentStatus, **fields) -> AgentRef | None:
        with self._lock:
            ref = self._refs.get(task_id)
            if ref is None:
                return None
            ref.status = status
            for key, value in fields.items():
                if hasattr(ref, key):
                    setattr(ref, key, value)
            handlers = list(self._change_handlers)
        for handler in handlers:
            try:
                handler(ref)
            except Exception:
                pass  # a subscriber must not break updates
        return ref

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._refs.pop(task_id, None)

    def on_status_change(self, handler: ChangeHandler) -> Callable[[], None]:
        with self._lock:
            self._change_handlers.append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                if handler in self._change_handlers:
                    self._change_handlers.remove(handler)

        return _unsubscribe


# Process-level singleton.
agent_registry = AgentRegistry()
