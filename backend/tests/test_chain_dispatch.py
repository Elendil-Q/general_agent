"""Tests for the /chain dispatch logic in the Gateway run service.

Covers ``_maybe_parse_chain_command`` (prefix detection + stripping),
``resolve_agent_factory`` (chain factory selection), and ``build_run_config``
(chain_name injection into configurable/context).
"""

from __future__ import annotations

import pytest

from app.gateway import services


def test_detects_chain_command_string_content():
    chain_name, stripped = services._maybe_parse_chain_command({"messages": [{"role": "user", "content": "/chain:research-pipeline write a report about X"}]})
    assert chain_name == "research-pipeline"
    assert stripped is not None
    assert stripped["messages"][-1]["content"] == "write a report about X"


def test_detects_chain_command_no_rest():
    chain_name, stripped = services._maybe_parse_chain_command({"messages": [{"role": "user", "content": "/chain:research-pipeline"}]})
    assert chain_name == "research-pipeline"
    assert stripped["messages"][-1]["content"] == ""


def test_detects_chain_command_block_content():
    chain_name, stripped = services._maybe_parse_chain_command({"messages": [{"role": "user", "content": [{"type": "text", "text": "/chain:foo do something"}]}]})
    assert chain_name == "foo"
    assert stripped["messages"][-1]["content"][0]["text"] == "do something"


def test_no_chain_command_returns_none():
    chain_name, stripped = services._maybe_parse_chain_command({"messages": [{"role": "user", "content": "just a normal message"}]})
    assert chain_name is None
    assert stripped is None


def test_none_input_returns_none():
    chain_name, stripped = services._maybe_parse_chain_command(None)
    assert chain_name is None
    assert stripped is None


def test_resume_input_is_none_so_no_detection():
    # On resume, body.input is None — no /chain: prefix to detect.
    chain_name, _ = services._maybe_parse_chain_command(None)
    assert chain_name is None


def test_chain_name_preserves_other_messages():
    chain_name, stripped = services._maybe_parse_chain_command(
        {
            "messages": [
                {"role": "user", "content": "earlier message"},
                {"role": "user", "content": "/chain:foo run it"},
            ]
        }
    )
    assert chain_name == "foo"
    assert len(stripped["messages"]) == 2
    assert stripped["messages"][0]["content"] == "earlier message"
    assert stripped["messages"][1]["content"] == "run it"


def test_resolve_factory_returns_chain_factory():
    factory = services.resolve_agent_factory(None, chain_name="research-pipeline")
    assert factory.__name__ == "make_chain_agent"


def test_resolve_factory_returns_lead_factory_without_chain():
    factory = services.resolve_agent_factory(None)
    assert factory.__name__ == "make_lead_agent"


def test_build_run_config_injects_chain_name():
    config = services.build_run_config("thread-1", None, None, chain_name="research-pipeline")
    assert config["configurable"]["chain_name"] == "research-pipeline"
    assert config["context"]["chain_name"] == "research-pipeline"


def test_build_run_config_without_chain_name():
    config = services.build_run_config("thread-1", None, None)
    assert "chain_name" not in config.get("configurable", {})
    assert "chain_name" not in config.get("context", {})


@pytest.mark.parametrize("invalid", ["/chain:", "/chain", "/chain:UI", "/chain_foo", "chain:foo"])
def test_invalid_chain_prefix_not_matched(invalid):
    chain_name, _ = services._maybe_parse_chain_command({"messages": [{"role": "user", "content": invalid}]})
    assert chain_name is None
