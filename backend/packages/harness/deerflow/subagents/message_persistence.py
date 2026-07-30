"""Persists subagent conversation messages into the run event store.

The subagent executor emits ``subagent:message`` events on its isolated
event-loop thread; the run event store (notably ``DbRunEventStore``, whose
SQLAlchemy async engine is bound to the Gateway main loop) cannot be awaited
from that thread. This module bridges the two: ``bind()`` captures the main
loop + store and subscribes to the event bus; ``_on_message`` schedules
``store.put`` back onto the main loop via ``asyncio.run_coroutine_threadsafe``
(fire-and-forget; failures are logged, never raised into the bus).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING

from deerflow.subagents.event_bus import event_bus

if TYPE_CHECKING:
    from deerflow.runtime.events.store.base import RunEventStore

logger = logging.getLogger(__name__)


class SubagentMessagePersister:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event_store: RunEventStore | None = None

    def bind(self, *, loop: asyncio.AbstractEventLoop, event_store: RunEventStore) -> Callable[[], None]:
        """Capture the main loop + store, subscribe to the event bus, return an unsubscribe callable."""
        self._loop = loop
        self._event_store = event_store
        return event_bus.on("subagent:message", self._on_message)

    def _on_message(self, payload: dict) -> None:
        """Event-bus handler (runs on arbitrary threads). Schedules ``store.put``
        onto the captured main loop — fire-and-forget with a done-callback that
        logs exceptions."""
        loop = self._loop
        store = self._event_store
        if loop is None or store is None:
            logger.debug("subagent:message received before bind; dropping")
            return

        from deerflow.subagents.agent_registry import agent_registry

        task_id = payload.get("task_id", "")
        ref = agent_registry.get(task_id)
        description = ref.description if ref is not None else ""

        message = payload.get("message")
        if not isinstance(message, dict):
            logger.warning(
                "Dropping subagent:message with non-dict message payload (task_id=%s, type=%s)",
                task_id,
                type(message).__name__,
            )
            return

        # Per-message store.put is a dedicated-transaction, low-frequency path
        # (one put per fresh subagent AI/Tool message). If subagent chatter
        # grows, coalescing via a put_batch drain is the planned follow-up.
        future = asyncio.run_coroutine_threadsafe(
            store.put(
                thread_id=payload.get("thread_id", ""),
                run_id=payload.get("run_id") or "subagent",
                event_type="subagent.message",
                category="subagent_message",
                content=message,
                metadata={
                    "task_id": task_id,
                    "subagent_type": payload.get("subagent_type", ""),
                    "description": description,
                },
            ),
            loop,
        )
        future.add_done_callback(self._log_failure)

    @staticmethod
    def _log_failure(future: Future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Failed to persist subagent message")


# Process-level singleton, same style as event_bus / sse_bridge.
subagent_message_persister = SubagentMessagePersister()
