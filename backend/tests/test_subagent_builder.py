"""Tests for ``deerflow.subagents.builder.build_subagent_agent``.

Focus: the compiled graph's ``name`` — it becomes the root span name in
Phoenix (OpenInference LangChain instrumentation names spans after the
LangChain run name), and the instrumentor's ``"agent" in run.name.lower()``
heuristic maps names containing "agent" to ``openinference.span.kind=AGENT``.
Naming the graph ``subagent:<name>`` therefore fixes both the span name and
the span kind for subagent traces.
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from deerflow.subagents.builder import build_subagent_agent
from deerflow.subagents.config import SubagentConfig


def _module(name: str, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def captured_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch the builder's collaborators and capture create_agent kwargs."""
    import deerflow.subagents.builder as builder_module

    captured: dict[str, dict] = {}

    def fake_create_chat_model(**kwargs):
        captured["model"] = kwargs
        return object()

    def fake_create_agent(**kwargs):
        captured["agent"] = kwargs
        return SimpleNamespace()

    def fake_build_subagent_runtime_middlewares(**kwargs):
        captured["middlewares"] = kwargs
        return []

    monkeypatch.setattr(builder_module, "create_chat_model", fake_create_chat_model)
    monkeypatch.setattr(builder_module, "create_agent", fake_create_agent)
    # build_subagent_runtime_middlewares is imported lazily inside
    # build_subagent_agent; replace the module so the fake is picked up
    # without importing the real (heavy) middleware module.
    monkeypatch.setitem(
        sys.modules,
        "deerflow.agents.middlewares.tool_error_handling_middleware",
        _module(
            "deerflow.agents.middlewares.tool_error_handling_middleware",
            build_subagent_runtime_middlewares=fake_build_subagent_runtime_middlewares,
        ),
    )
    return captured


def _build(name: str, captured: dict):
    config = SubagentConfig(name=name, description="test subagent")
    app_config = SimpleNamespace(models=[SimpleNamespace(name="default-model")])
    agent = build_subagent_agent(
        config,
        tools=[],
        app_config=app_config,
        model_name="default-model",
    )
    return agent


class TestSubagentGraphNaming:
    def test_graph_named_after_subagent(self, captured_kwargs):
        _build("general-purpose", captured_kwargs)
        assert captured_kwargs["agent"]["name"] == "subagent:general-purpose"

    def test_graph_name_normalizes_like_trace_name(self, captured_kwargs):
        """Matches the executor's trace-name normalization: strip, lowercase,
        underscores to hyphens."""
        _build("  My_Agent ", captured_kwargs)
        assert captured_kwargs["agent"]["name"] == "subagent:my-agent"

    def test_graph_name_fallback_when_blank(self, captured_kwargs):
        _build("   ", captured_kwargs)
        assert captured_kwargs["agent"]["name"] == "subagent"

    def test_graph_name_contains_agent_for_phoenix_kind_heuristic(self, captured_kwargs):
        """Pin the invariant the Phoenix/OpenInference AGENT-kind heuristic
        relies on: the root span name must contain "agent"."""
        _build("general-purpose", captured_kwargs)
        assert "agent" in captured_kwargs["agent"]["name"].lower()
