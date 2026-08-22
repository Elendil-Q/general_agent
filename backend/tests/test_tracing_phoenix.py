"""Tests for the Arize Phoenix tracing integration.

Phoenix is an optional dependency (``deerflow-harness[phoenix]``) and all
imports inside ``deerflow.tracing.phoenix`` are lazy, so this suite must pass
without ``arize-phoenix-otel`` / ``openinference`` installed. Tests that
exercise registration inject fake modules into ``sys.modules`` instead of
importing the real packages.
"""

from __future__ import annotations

import builtins
import contextlib
import sys
import types
from types import SimpleNamespace

import pytest

from deerflow.config.tracing_config import (
    get_enabled_tracing_providers,
    get_tracing_config,
    reset_tracing_config,
)
from deerflow.tracing import phoenix as phoenix_module


@pytest.fixture(autouse=True)
def _reset_phoenix_state(monkeypatch):
    """Clear every tracing env var and reset the module's registration flag.

    ``register_phoenix_tracing`` is a process-global side effect, so the flag
    must be torn down between tests or idempotency assertions would observe a
    pre-registered state from an earlier test.
    """
    for name in (
        "PHOENIX_TRACING",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "PHOENIX_PROJECT_NAME",
        "PHOENIX_HEADERS",
        "LANGFUSE_TRACING",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_TRACING",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_tracing_config()
    phoenix_module.shutdown_phoenix_tracing()
    yield
    reset_tracing_config()
    phoenix_module.shutdown_phoenix_tracing()


def _enable_phoenix(monkeypatch, *, endpoint: str | None = None, project: str | None = None) -> None:
    monkeypatch.setenv("PHOENIX_TRACING", "true")
    if endpoint:
        monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", endpoint)
    if project:
        monkeypatch.setenv("PHOENIX_PROJECT_NAME", project)
    reset_tracing_config()


def test_register_returns_false_when_disabled():
    assert phoenix_module.register_phoenix_tracing() is False


def test_register_is_idempotent(monkeypatch):
    _enable_phoenix(monkeypatch)

    calls: list[object] = []
    monkeypatch.setattr(phoenix_module, "_do_register", lambda cfg: calls.append(cfg))

    assert phoenix_module.register_phoenix_tracing() is True
    assert phoenix_module.register_phoenix_tracing() is True

    assert len(calls) == 1


def test_register_raises_helpful_error_when_extra_missing(monkeypatch):
    _enable_phoenix(monkeypatch)

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "phoenix.otel":
            raise ImportError("No module named 'phoenix.otel'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    cfg = SimpleNamespace(project_name="deer-flow", endpoint="http://localhost:6006/v1/traces", headers={})
    with pytest.raises(RuntimeError, match=r"deerflow-harness\[phoenix\]"):
        phoenix_module._do_register(cfg)


def test_register_passes_config_to_phoenix(monkeypatch):
    fake_module = types.ModuleType("phoenix.otel")
    captured: dict = {}

    def _fake_register(**kwargs):
        captured.update(kwargs)

    fake_module.register = _fake_register
    monkeypatch.setitem(sys.modules, "phoenix.otel", fake_module)

    cfg = SimpleNamespace(
        project_name="my-project",
        endpoint="http://collector:4317/v1/traces",
        headers={"Authorization": "Bearer secret"},
    )
    phoenix_module._do_register(cfg)

    assert captured["project_name"] == "my-project"
    assert captured["endpoint"] == "http://collector:4317/v1/traces"
    assert captured["auto_instrument"] is True
    assert captured["headers"] == {"Authorization": "Bearer secret"}


def test_register_omits_headers_when_empty(monkeypatch):
    fake_module = types.ModuleType("phoenix.otel")
    captured: dict = {}

    def _fake_register(**kwargs):
        captured.update(kwargs)

    fake_module.register = _fake_register
    monkeypatch.setitem(sys.modules, "phoenix.otel", fake_module)

    cfg = SimpleNamespace(project_name="p", endpoint="http://x/v1/traces", headers={})
    phoenix_module._do_register(cfg)

    assert "headers" not in captured


def test_config_defaults_when_unset(monkeypatch):
    cfg = get_tracing_config().phoenix
    assert cfg.endpoint == "http://localhost:6006/v1/traces"
    assert cfg.project_name == "deer-flow"


def test_config_reload_after_reset(monkeypatch):
    assert "phoenix" not in get_enabled_tracing_providers()

    _enable_phoenix(monkeypatch, endpoint="http://custom:1234/v1/traces", project="my-project")
    monkeypatch.setenv("PHOENIX_HEADERS", '{"Authorization": "Bearer abc"}')

    assert "phoenix" in get_enabled_tracing_providers()
    cfg = get_tracing_config().phoenix
    assert cfg.endpoint == "http://custom:1234/v1/traces"
    assert cfg.project_name == "my-project"
    assert cfg.headers == {"Authorization": "Bearer abc"}


def test_build_metadata_returns_empty_when_disabled():
    from deerflow.tracing import metadata as tracing_metadata

    result = tracing_metadata.build_phoenix_trace_metadata(thread_id="t-1", user_id="u-1")
    assert result == {}


def test_build_metadata_when_enabled(monkeypatch):
    from deerflow.tracing import metadata as tracing_metadata

    _enable_phoenix(monkeypatch)

    result = tracing_metadata.build_phoenix_trace_metadata(
        thread_id="thread-abc",
        user_id="user-42",
        assistant_id="lead-agent",
        model_name="gpt-4o",
        environment="production",
    )

    assert result["session_id"] == "thread-abc"
    assert result["thread_id"] == "thread-abc"
    assert result["user_id"] == "user-42"
    assert result["trace_name"] == "lead-agent"
    assert result["model_name"] == "gpt-4o"
    assert result["environment"] == "production"


def test_build_metadata_user_falls_back_to_default(monkeypatch):
    from deerflow.tracing import metadata as tracing_metadata

    _enable_phoenix(monkeypatch)

    result = tracing_metadata.build_phoenix_trace_metadata(thread_id="thread-abc", user_id=None)
    assert result["user_id"] == "default"


def test_build_metadata_omits_optional_keys(monkeypatch):
    from deerflow.tracing import metadata as tracing_metadata

    _enable_phoenix(monkeypatch)

    result = tracing_metadata.build_phoenix_trace_metadata(thread_id="thread-abc", user_id="u")
    assert "trace_name" not in result
    assert "model_name" not in result
    assert "environment" not in result


def test_inject_trace_metadata_fans_out_to_both_providers(monkeypatch):
    from deerflow.tracing import metadata as tracing_metadata

    _enable_phoenix(monkeypatch)
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    reset_tracing_config()

    config: dict = {}
    tracing_metadata.inject_trace_metadata(
        config,
        thread_id="thread-fan",
        user_id="u-1",
        assistant_id="lead-agent",
    )

    metadata = config["metadata"]
    # Phoenix keys
    assert metadata["session_id"] == "thread-fan"
    assert metadata["thread_id"] == "thread-fan"
    # Langfuse keys
    assert metadata["langfuse_session_id"] == "thread-fan"
    assert metadata["langfuse_user_id"] == "u-1"


def test_inject_trace_metadata_respects_caller_overrides(monkeypatch):
    from deerflow.tracing import metadata as tracing_metadata

    _enable_phoenix(monkeypatch)

    config = {"metadata": {"session_id": "explicit-session"}}
    tracing_metadata.inject_trace_metadata(config, thread_id="thread-abc", user_id="u-1")

    metadata = config["metadata"]
    # Caller-supplied key wins via ``setdefault``.
    assert metadata["session_id"] == "explicit-session"
    assert metadata["thread_id"] == "thread-abc"


def test_span_context_noop_when_disabled():
    with phoenix_module.phoenix_span_context(session_id="s", user_id="u"):
        pass


def test_span_context_noop_when_dependency_missing(monkeypatch):
    _enable_phoenix(monkeypatch)

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "openinference.instrumentation":
            raise ImportError("openinference not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with phoenix_module.phoenix_span_context(session_id="s", user_id="u"):
        pass


def test_span_context_applies_attributes_when_enabled(monkeypatch):
    _enable_phoenix(monkeypatch)

    fake_module = types.ModuleType("openinference.instrumentation")
    applied: list[dict[str, str]] = []

    @contextlib.contextmanager
    def _fake_using_attributes(**attributes):
        applied.append(attributes)
        yield

    fake_module.using_attributes = _fake_using_attributes
    monkeypatch.setitem(sys.modules, "openinference.instrumentation", fake_module)

    with phoenix_module.phoenix_span_context(session_id="sess-1", user_id="u-1"):
        pass

    assert applied == [{"session_id": "sess-1", "user_id": "u-1"}]


def test_span_context_skips_empty_attributes(monkeypatch):
    _enable_phoenix(monkeypatch)

    fake_module = types.ModuleType("openinference.instrumentation")
    applied: list[dict[str, str]] = []

    @contextlib.contextmanager
    def _fake_using_attributes(**attributes):
        applied.append(attributes)
        yield

    fake_module.using_attributes = _fake_using_attributes
    monkeypatch.setitem(sys.modules, "openinference.instrumentation", fake_module)

    # Neither session nor user supplied → nothing to apply.
    with phoenix_module.phoenix_span_context():
        pass

    assert applied == []
