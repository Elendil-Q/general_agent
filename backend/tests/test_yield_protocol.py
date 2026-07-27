# backend/tests/test_yield_protocol.py
"""Tests for the yield protocol assembly and schema validation."""

from __future__ import annotations

from deerflow.subagents.yield_protocol import (
    YieldCollector,
    assembleYieldResult,
)


def test_terminal_payload_overrides_incremental():
    c = YieldCollector(output_schema=None)
    c.record({"findings": ["a"]}, ["findings"])  # incremental
    c.record({"summary": "done"}, None)  # terminal
    assembled = assembleYieldResult(c.yields, None)
    assert assembled["data"] == {"summary": "done"}
    assert assembled["schemaValid"] is True


def test_incremental_sections_accumulate():
    c = YieldCollector(output_schema=None)
    c.record({"issue": "x"}, ["findings"])
    c.record({"issue": "y"}, ["findings"])
    c.record({}, None)  # terminal with empty data
    assembled = assembleYieldResult(c.yields, None)
    # incremental findings accumulate into a list under their section name
    assert assembled["data"]["findings"] == [{"issue": "x"}, {"issue": "y"}]


def test_no_terminal_falls_back_to_none():
    c = YieldCollector(output_schema=None)
    c.record({"a": 1}, ["section"])
    assembled = assembleYieldResult(c.yields, None)
    # no terminal -> data is the accumulated sections only (no terminal payload)
    assert assembled["data"] == {"section": [{"a": 1}]}
    assert assembled["schemaValid"] is True


def test_schema_validation_pass():
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    c = YieldCollector(output_schema=schema)
    c.record({"summary": "ok"}, None)
    assembled = assembleYieldResult(c.yields, schema)
    assert assembled["schemaValid"] is True
    assert assembled["schemaErrors"] is None


def test_schema_validation_fail_degrades_gracefully():
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    c = YieldCollector(output_schema=schema)
    c.record({"wrong": "field"}, None)  # missing 'summary'
    assembled = assembleYieldResult(c.yields, schema)
    assert assembled["schemaValid"] is False
    assert assembled["data"] == {"wrong": "field"}  # payload kept
    assert len(assembled["schemaErrors"]) > 0


def test_has_terminal_reflects_records():
    c = YieldCollector(output_schema=None)
    assert c.has_terminal() is False
    c.record({"x": 1}, ["section"])
    assert c.has_terminal() is False
    c.record({"done": True}, None)
    assert c.has_terminal() is True


def test_falsy_scalar_terminal_preserved():
    """Falsy scalars (0, False, '') must NOT be replaced by accumulated sections."""
    for falsy_value in (0, False, ""):
        c = YieldCollector(output_schema=None)
        c.record({"item": "accumulated"}, ["section"])
        c.record(falsy_value, None)  # terminal with a falsy scalar
        assembled = assembleYieldResult(c.yields, None)
        assert assembled["data"] == falsy_value, f"falsy scalar {falsy_value!r} was overwritten"
