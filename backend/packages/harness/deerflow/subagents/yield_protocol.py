# backend/packages/harness/deerflow/subagents/yield_protocol.py
"""Structured-output yield protocol for subagents.

A subagent submits results via the ``yield`` tool instead of relying on
the implicit "last AIMessage" extraction. Yields come in two shapes:

- **terminal** (``type`` omitted or ``str``): the final payload; overrides
  any prior incremental sections.
- **incremental** (``type`` is ``list[str]``): a named section that
  accumulates into an array (e.g. a reviewer yielding one finding at a
  time).

``assembleYieldResult`` folds the sequence into the final payload and,
when an ``output`` JSON Schema is declared, validates the terminal
payload. Validation failure degrades gracefully: the payload is kept but
flagged ``schemaValid: False`` with the error list.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class YieldEntry:
    data: Any
    type: str | list[str] | None


class YieldCollector:
    """Thread-safe collector of yield entries for one subagent run."""

    def __init__(self, *, output_schema: dict | None = None) -> None:
        self._output_schema = output_schema
        self._entries: list[YieldEntry] = []
        self._lock = Lock()

    def record(self, data: Any, type: str | list[str] | None) -> None:
        with self._lock:
            self._entries.append(YieldEntry(data=data, type=type))

    @property
    def yields(self) -> list[YieldEntry]:
        with self._lock:
            return list(self._entries)

    def has_terminal(self) -> bool:
        with self._lock:
            return any(e.type is None or isinstance(e.type, str) for e in self._entries)


def assembleYieldResult(yields: list[YieldEntry], output_schema: dict | None) -> dict:
    """Fold a yield sequence into ``{data, schemaValid, schemaErrors?}``.

    Incremental sections (``type: list``) accumulate into arrays keyed by
    their section name. The terminal payload (last ``type`` omitted/str)
    overrides incremental accumulation. When ``output_schema`` is set, the
    terminal payload is validated; failure keeps the payload but flags it.
    """
    accumulated: dict[str, list] = {}
    terminal: Any = None
    has_terminal = False

    for entry in yields:
        if entry.type is None or isinstance(entry.type, str):
            terminal = entry.data
            has_terminal = True
        elif isinstance(entry.type, list):
            for name in entry.type:
                accumulated.setdefault(name, []).append(entry.data)

    if has_terminal:
        data = terminal
        # a non-empty terminal payload overrides incremental sections;
        # an empty/None terminal falls back to the accumulated sections
        if not data and accumulated:
            data = dict(accumulated)
    else:
        data = dict(accumulated) if accumulated else None

    if output_schema is None:
        return {"data": data, "schemaValid": True, "schemaErrors": None}

    try:
        import jsonschema  # type: ignore

        jsonschema.validate(instance=data, schema=output_schema)
        return {"data": data, "schemaValid": True, "schemaErrors": None}
    except Exception as exc:  # jsonschema.ValidationError or import error
        errors = [str(exc)] if not isinstance(exc, ImportError) else ["jsonschema not installed"]
        if hasattr(exc, "message"):
            errors = [str(getattr(exc, "message", exc))]
        return {"data": data, "schemaValid": False, "schemaErrors": errors}


_yield_collector_ctx: contextvars.ContextVar[YieldCollector | None] = contextvars.ContextVar("deerflow_yield_collector", default=None)
