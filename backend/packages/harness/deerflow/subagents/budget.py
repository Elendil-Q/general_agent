# backend/packages/harness/deerflow/subagents/budget.py
"""Soft/hard LLM-request budget for subagents.

Replaces the coarse ``max_turns`` cap with a finer request-count budget.
``soft`` is the wrap-up threshold (a SystemMessage nudges the subagent to
finish); ``hard = ceil(1.5 * soft)`` force-terminates. ``None`` disables
the budget path entirely (today's behavior).
"""

from __future__ import annotations

import math
from threading import Lock

from deerflow.subagents.event_bus import event_bus


class BudgetMonitor:
    """Tracks LLM request count against a soft/hard budget."""

    def __init__(self, *, task_id: str, thread_id: str, soft: int | None) -> None:
        self._task_id = task_id
        self._thread_id = thread_id
        self._soft = soft
        self._hard = math.ceil(soft * 1.5) if soft is not None else None
        self._requests = 0
        self._lock = Lock()
        self._warned = False

    @property
    def requests(self) -> int:
        with self._lock:
            return self._requests

    def tick(self) -> None:
        with self._lock:
            self._requests += 1
            requests = self._requests
            soft = self._soft
            hard = self._hard
            warned = self._warned
        if soft is None:
            return
        if not warned and requests >= soft:
            with self._lock:
                self._warned = True
            event_bus.emit(
                "subagent:budget",
                {
                    "event": "warning",
                    "task_id": self._task_id,
                    "thread_id": self._thread_id,
                    "requests": requests,
                    "limit": soft,
                },
            )
        if hard is not None and requests >= hard:
            event_bus.emit(
                "subagent:budget",
                {
                    "event": "exceeded",
                    "task_id": self._task_id,
                    "thread_id": self._thread_id,
                    "requests": requests,
                    "limit": hard,
                },
            )

    def at_soft_limit(self) -> bool:
        if self._soft is None:
            return False
        return self.requests >= self._soft

    def at_hard_limit(self) -> bool:
        if self._hard is None:
            return False
        return self.requests >= self._hard
