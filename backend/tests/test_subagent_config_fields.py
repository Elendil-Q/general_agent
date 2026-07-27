# backend/tests/test_subagent_config_fields.py
"""Tests for the new SubagentConfig v1 fields."""

from __future__ import annotations

from deerflow.subagents.config import SubagentConfig


def test_defaults_preserve_existing_behavior():
    c = SubagentConfig(name="x", description="d")
    assert c.keep_alive is False
    assert c.max_requests is None
    assert c.output is None


def test_keep_alive_opt_in():
    c = SubagentConfig(name="x", description="d", keep_alive=True)
    assert c.keep_alive is True


def test_max_requests_soft_budget():
    c = SubagentConfig(name="x", description="d", max_requests=200)
    assert c.max_requests == 200


def test_output_schema_optional():
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    c = SubagentConfig(name="x", description="d", output=schema)
    assert c.output == schema
