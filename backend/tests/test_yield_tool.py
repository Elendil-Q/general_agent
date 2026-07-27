# backend/tests/test_yield_tool.py
"""Tests for the yield tool's recording and return messages."""

from __future__ import annotations

import pytest

from deerflow.subagents.yield_protocol import (
    YieldCollector,
    _yield_collector_ctx,
)
from deerflow.tools.builtins.yield_tool import yield_tool


@pytest.fixture
def collector_ctx():
    collector = YieldCollector(output_schema=None)
    token = _yield_collector_ctx.set(collector)
    yield collector
    _yield_collector_ctx.reset(token)


def _call(data, type=None):
    # The tool is a StructuredTool; invoke its underlying func directly.
    func = yield_tool.func if hasattr(yield_tool, "func") else yield_tool
    return func(data=data, type=type)


def test_terminal_yield_records_and_confirms(collector_ctx):
    msg = _call({"summary": "done"}, type=None)
    assert collector_ctx.has_terminal() is True
    assert "submitted" in msg.lower() or "stop" in msg.lower()


def test_incremental_yield_records_section(collector_ctx):
    msg = _call({"issue": "x"}, type=["findings"])
    assert collector_ctx.has_terminal() is False
    assert len(collector_ctx.yields) == 1
    assert "section" in msg.lower() or "recorded" in msg.lower()


def test_yield_without_collector_returns_error():
    # No collector set in this context
    token = _yield_collector_ctx.set(None)
    try:
        msg = _call({"x": 1}, type=None)
        assert "error" in msg.lower() or "no collector" in msg.lower()
    finally:
        _yield_collector_ctx.reset(token)
