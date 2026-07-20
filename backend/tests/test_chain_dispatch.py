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


# ---------------------------------------------------------------------------
# /chain-resume:<name> parsing
# ---------------------------------------------------------------------------


def test_detects_chain_resume_command():
    name = services._maybe_parse_chain_resume_command({"messages": [{"role": "user", "content": "/chain-resume:example-pipeline"}]})
    assert name == "example-pipeline"


def test_chain_resume_command_with_trailing_text_returns_name():
    # The command consumes the whole message, but trailing text is tolerated
    # (only the name is extracted).
    name = services._maybe_parse_chain_resume_command({"messages": [{"role": "user", "content": "/chain-resume:foo  "}]})
    assert name == "foo"


def test_chain_resume_command_not_triggered_for_plain_chain():
    name = services._maybe_parse_chain_resume_command({"messages": [{"role": "user", "content": "/chain:foo run it"}]})
    assert name is None


def test_chain_resume_command_none_input():
    assert services._maybe_parse_chain_resume_command(None) is None


@pytest.mark.parametrize("invalid", ["/chain-resume:", "/chain-resume", "/chain-resume:UI", "/chain_resume:foo"])
def test_invalid_chain_resume_prefix_not_matched(invalid):
    assert services._maybe_parse_chain_resume_command({"messages": [{"role": "user", "content": invalid}]}) is None


# ---------------------------------------------------------------------------
# build_run_config chain_resume injection
# ---------------------------------------------------------------------------


def test_build_run_config_injects_chain_resume():
    payload = {"completed_nodes": {"a": "1"}, "input": "orig"}
    config = services.build_run_config("thread-1", None, None, chain_name="foo", chain_resume=payload)
    assert config["configurable"]["chain_resume"] == payload
    assert config["context"]["chain_resume"] == payload


def test_build_run_config_without_chain_resume():
    config = services.build_run_config("thread-1", None, None, chain_name="foo")
    assert "chain_resume" not in config.get("configurable", {})


# ---------------------------------------------------------------------------
# _chain_done_callback status mapping
# ---------------------------------------------------------------------------


def _make_record(status, *, abort=False, abort_action="interrupt"):
    import asyncio
    from types import SimpleNamespace

    record = SimpleNamespace(
        status=status,
        abort_event=SimpleNamespace(is_set=lambda: abort),
        abort_action=abort_action,
        run_id="run-1",
    )
    # Attach a real asyncio.Event-shaped object is unnecessary; is_set is mocked.
    _ = asyncio
    return record


class _StubStore:
    def __init__(self):
        self.calls = []

    def mark_status(self, run_id, status):
        self.calls.append(("mark_status", run_id, status))

    def mark_completed_if_done(self, run_id):
        self.calls.append(("mark_completed_if_done", run_id))


def test_done_callback_abort_marks_interrupted():
    from deerflow.runtime import RunStatus

    store = _StubStore()
    record = _make_record(RunStatus.interrupted, abort=True)
    services._chain_done_callback(record, store, "run-1")
    assert store.calls == [("mark_status", "run-1", "interrupted")]


def test_done_callback_rollback_stays_recoverable():
    # rollback surfaces as abort + error status; progress must stay interrupted.
    from deerflow.runtime import RunStatus

    store = _StubStore()
    record = _make_record(RunStatus.error, abort=True, abort_action="rollback")
    services._chain_done_callback(record, store, "run-1")
    assert store.calls == [("mark_status", "run-1", "interrupted")]


def test_done_callback_error_marks_failed():
    from deerflow.runtime import RunStatus

    store = _StubStore()
    record = _make_record(RunStatus.error, abort=False)
    services._chain_done_callback(record, store, "run-1")
    assert store.calls == [("mark_status", "run-1", "failed")]


def test_done_callback_success_marks_completed_if_done():
    from deerflow.runtime import RunStatus

    store = _StubStore()
    record = _make_record(RunStatus.success, abort=False)
    services._chain_done_callback(record, store, "run-1")
    assert store.calls == [("mark_completed_if_done", "run-1")]


def test_done_callback_swallows_exceptions():
    class _BoomStore:
        def mark_status(self, *a, **k):
            raise RuntimeError("boom")

        def mark_completed_if_done(self, *a, **k):
            raise RuntimeError("boom")

    from deerflow.runtime import RunStatus

    record = _make_record(RunStatus.success, abort=False)
    # Must not raise.
    services._chain_done_callback(record, _BoomStore(), "run-1")


# ---------------------------------------------------------------------------
# _validate_chain_resume
# ---------------------------------------------------------------------------


def test_validate_chain_resume_missing_raises_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        services._validate_chain_resume(None, "foo")
    assert exc.value.status_code == 404


def test_validate_chain_resume_completed_raises_409():
    from types import SimpleNamespace

    from fastapi import HTTPException

    progress = SimpleNamespace(status="completed")
    with pytest.raises(HTTPException) as exc:
        services._validate_chain_resume(progress, "foo")
    assert exc.value.status_code == 409


def test_validate_chain_resume_running_ok():
    from types import SimpleNamespace

    services._validate_chain_resume(SimpleNamespace(status="interrupted"), "foo")
