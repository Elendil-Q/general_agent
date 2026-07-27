"""Process-level pub/sub for subagent lifecycle/progress/budget events.

Holds no state - pure event broadcast. Subscribers register per channel;
emit() synchronously invokes every handler for that channel. Handler
exceptions are caught and logged so one bad subscriber cannot poison
others.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from threading import Lock

logger = logging.getLogger(__name__)

Handler = Callable[[dict], None]


class EventBus:
    """Synchronous in-process pub/sub keyed by channel name."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = Lock()

    def on(self, channel: str, handler: Handler) -> Callable[[], None]:
        """Subscribe ``handler`` to ``channel``. Returns an unsubscribe callable."""
        with self._lock:
            self._handlers[channel].append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(channel)
                if handlers and handler in handlers:
                    handlers.remove(handler)

        return _unsubscribe

    def emit(self, channel: str, payload: dict) -> None:
        """Broadcast ``payload`` to every subscriber of ``channel``.

        Each handler is invoked synchronously; exceptions are logged and
        swallowed so a failing subscriber cannot block or poison others.
        """
        with self._lock:
            handlers = list(self._handlers.get(channel, ()))
        for handler in handlers:
            try:
                handler(payload)
            except Exception:
                logger.exception("EventBus handler %r raised on channel %s", handler, channel)


# Process-level singleton. Importable everywhere; no need to pass around.
event_bus = EventBus()
