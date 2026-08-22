"""Langfuse trace-attribute metadata builders.

The Langfuse v4 ``langchain.CallbackHandler`` lifts a fixed set of reserved
keys from ``RunnableConfig.metadata`` onto the root trace:

- ``langfuse_session_id`` → groups traces (LangGraph thread → Langfuse Session)
- ``langfuse_user_id``    → trace user_id (powers the Users page)
- ``langfuse_trace_name`` → human-readable trace name
- ``langfuse_tags``       → trace tags

See ``langfuse/langchain/CallbackHandler.py::_parse_langfuse_trace_attributes``
and https://langfuse.com/docs/observability/features/sessions for the
contract. Builders here exist so the gateway/run worker can inject the
right metadata without leaking Langfuse internals into the call sites.
"""

from __future__ import annotations

from typing import Any

from deerflow.config import get_enabled_tracing_providers

# Lazy-imported below to avoid a circular import: ``deerflow.runtime`` eagerly
# imports the run worker, which in turn needs ``deerflow.tracing``.
_DEFAULT_TRACE_NAME = "lead-agent"


def build_langfuse_trace_metadata(
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Return Langfuse trace-attribute metadata for ``RunnableConfig.metadata``.

    Returns ``{}`` when Langfuse is not in the enabled tracing providers so
    callers can unconditionally merge the result without affecting LangSmith
    or other tracers.

    Args:
        thread_id: LangGraph thread id; mapped to ``langfuse_session_id``.
        user_id: Effective user id; falls back to ``DEFAULT_USER_ID`` when
            ``None`` so the Langfuse Users page works in no-auth mode.
        assistant_id: Optional agent identifier; defaults to ``"lead-agent"``.
        model_name: Model name; emitted as ``model:<name>`` in ``langfuse_tags``.
        environment: Deployment env (e.g. ``"production"``); emitted as
            ``env:<value>`` in ``langfuse_tags``.
    """
    if "langfuse" not in get_enabled_tracing_providers():
        return {}

    from deerflow.runtime.user_context import DEFAULT_USER_ID

    metadata: dict[str, Any] = {
        "langfuse_session_id": thread_id,
        "langfuse_user_id": user_id or DEFAULT_USER_ID,
        "langfuse_trace_name": assistant_id or _DEFAULT_TRACE_NAME,
    }

    tags: list[str] = []
    if environment:
        tags.append(f"env:{environment}")
    if model_name:
        tags.append(f"model:{model_name}")
    if tags:
        metadata["langfuse_tags"] = tags

    return metadata


def inject_langfuse_metadata(
    config: dict,
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> None:
    """Merge Langfuse trace-attribute metadata into ``config["metadata"]``.

    Shared by the gateway worker (``runtime/runs/worker.py``) and the
    embedded client (``client.py``) so the two paths cannot drift apart.

    Caller-supplied metadata wins via ``setdefault`` — an upstream value
    for e.g. ``langfuse_session_id`` set by the frontend stays untouched.
    The ``config`` dict is mutated in place; the call is a no-op when
    Langfuse is not in the enabled tracing providers.
    """
    langfuse_metadata = build_langfuse_trace_metadata(
        thread_id=thread_id,
        user_id=user_id,
        assistant_id=assistant_id,
        model_name=model_name,
        environment=environment,
    )
    if not langfuse_metadata:
        return

    merged_metadata = dict(config.get("metadata") or {})
    for key, value in langfuse_metadata.items():
        merged_metadata.setdefault(key, value)
    config["metadata"] = merged_metadata


def build_phoenix_trace_metadata(
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Return OpenInference-compatible trace metadata for ``RunnableConfig.metadata``.

    The OpenInference LangChain tracer lifts ``session_id`` (or ``thread_id``)
    from ``run.extra.metadata`` onto the root span's ``session.id`` attribute
    (see its ``_metadata``), which Phoenix uses to group multi-turn traces
    into sessions. Returns ``{}`` when Phoenix is not in the enabled tracing
    providers so callers can unconditionally merge the result.
    """
    if "phoenix" not in get_enabled_tracing_providers():
        return {}

    from deerflow.runtime.user_context import DEFAULT_USER_ID

    metadata: dict[str, Any] = {
        "session_id": thread_id,
        "thread_id": thread_id,
        "user_id": user_id or DEFAULT_USER_ID,
    }
    if assistant_id:
        metadata["trace_name"] = assistant_id
    if model_name:
        metadata["model_name"] = model_name
    if environment:
        metadata["environment"] = environment
    return metadata


def inject_phoenix_metadata(
    config: dict,
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> None:
    """Merge OpenInference trace metadata into ``config["metadata"]``.

    Same ``setdefault`` (caller wins) semantics as the Langfuse injection so
    an upstream ``session_id`` override survives. A no-op when Phoenix is not
    in the enabled tracing providers.
    """
    phoenix_metadata = build_phoenix_trace_metadata(
        thread_id=thread_id,
        user_id=user_id,
        assistant_id=assistant_id,
        model_name=model_name,
        environment=environment,
    )
    if not phoenix_metadata:
        return

    merged_metadata = dict(config.get("metadata") or {})
    for key, value in phoenix_metadata.items():
        merged_metadata.setdefault(key, value)
    config["metadata"] = merged_metadata


def inject_trace_metadata(
    config: dict,
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> None:
    """Fan out trace-attribute metadata to all metadata-driven providers.

    Single entry point shared by the gateway worker (``runtime/runs/worker.py``),
    the embedded client (``client.py``) and the subagent executor so the
    provider set cannot drift between call sites. Currently dispatches to the
    Langfuse and Phoenix builders; each is a no-op when its provider is
    disabled.
    """
    inject_langfuse_metadata(
        config,
        thread_id=thread_id,
        user_id=user_id,
        assistant_id=assistant_id,
        model_name=model_name,
        environment=environment,
    )
    inject_phoenix_metadata(
        config,
        thread_id=thread_id,
        user_id=user_id,
        assistant_id=assistant_id,
        model_name=model_name,
        environment=environment,
    )
