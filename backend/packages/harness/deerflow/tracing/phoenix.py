"""Arize Phoenix tracing integration.

Modern Phoenix (``arize-phoenix-otel``) is process-global OpenTelemetry
instrumentation rather than a per-run LangChain callback handler. Calling
``register()`` once configures the global ``TracerProvider`` and
auto-instruments the installed ``openinference`` packages, so every
LangChain / LangGraph run in the process is traced automatically.

DeerFlow hooks registration into :func:`build_tracing_callbacks`: because
that function runs at every graph-root invocation, the first run lazily
registers Phoenix with zero call-site changes. Phoenix contributes no
callback handlers to the returned list — global instrumentation would
otherwise double-trace every span.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from typing import Any

from deerflow.config import get_enabled_tracing_providers, get_tracing_config

logger = logging.getLogger(__name__)

_register_lock = threading.Lock()
_registered = False


def register_phoenix_tracing() -> bool:
    """Idempotently register Phoenix as the global OTel tracer provider.

    Returns ``True`` when Phoenix is (or becomes) registered, ``False`` when
    it is not among the enabled tracing providers. Registration is a
    process-global side effect, so repeated calls are no-ops guarded by a
    lock plus a module-level flag.
    """
    global _registered
    if _registered:
        return True
    if "phoenix" not in get_enabled_tracing_providers():
        return False
    with _register_lock:
        if _registered:
            return True
        _do_register(get_tracing_config().phoenix)
        _registered = True
        return True


def _do_register(config: Any) -> None:
    try:
        from phoenix.otel import register
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("Phoenix tracing is enabled but the 'phoenix' extra is not installed. Install it with: uv add 'deerflow-harness[phoenix]'") from exc

    kwargs: dict[str, Any] = {
        "project_name": config.project_name,
        "endpoint": config.endpoint,
        "auto_instrument": True,
    }
    headers = getattr(config, "headers", None) or {}
    if headers:
        kwargs["headers"] = headers
    try:
        register(**kwargs)
    except Exception as exc:  # pragma: no cover - exercised via tests with monkeypatch
        logger.error("Phoenix tracing registration failed: %s", exc)
        raise


def shutdown_phoenix_tracing() -> None:
    """Best-effort flush/shutdown of the Phoenix tracer.

    Used by tests and clean shutdown paths; never raises. Resets the
    registration flag so a later run can register again.
    """
    global _registered
    if not _registered:
        return
    try:
        from phoenix.otel import tracer_provider

        provider = tracer_provider()
        if provider is not None and hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:  # pragma: no cover - best-effort teardown
        logger.exception("Phoenix tracer shutdown failed (ignored)")
    finally:
        _registered = False


@contextlib.contextmanager
def phoenix_span_context(*, session_id: str | None = None, user_id: str | None = None) -> Iterator[None]:
    """Apply OpenInference span attributes to a graph run.

    The OpenInference LangChain tracer reads ``session_id`` / ``thread_id``
    from ``RunnableConfig.metadata`` for the root span, but user attribution
    requires ``user.id`` set via the OTel context. This context manager wraps
    a graph invocation so every span created while active carries the
    attributes (``session.id`` / ``user.id``), matching how the Langfuse
    metadata path groups traces by thread and user.

    Degrades to a no-op when Phoenix is disabled or the optional dependency
    is missing, so callers can wrap unconditionally.
    """
    if "phoenix" not in get_enabled_tracing_providers():
        yield
        return
    try:
        from openinference.instrumentation import using_attributes
    except ImportError:  # pragma: no cover - depends on optional extra
        yield
        return

    if not session_id and not user_id:
        yield
        return
    # using_attributes accepts semantic kwargs (session_id/user_id) and maps them
    # to the OpenInference span attributes ``session.id`` / ``user.id``.
    with using_attributes(session_id=session_id or "", user_id=user_id or ""):
        yield
