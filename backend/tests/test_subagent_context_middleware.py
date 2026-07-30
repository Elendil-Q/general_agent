"""Tests for SubagentContextMiddleware.

Consolidates tests from the deleted ``test_subagent_limit_middleware.py`` and
``test_pending_task_guard_middleware.py`` plus new coverage for the
``SubagentStatusMessage.from_refs`` status-injection path and the
``awrap_model_call`` transient-context injection path.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.subagent_context_middleware import (
    SubagentContextMiddleware,
    SubagentStatusMessage,
)
from deerflow.subagents.executor import SubagentStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(thread_id: str = "test-thread") -> MagicMock:
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id} if thread_id else {}
    return runtime


def _make_request(messages, runtime):
    """Build a minimal ModelRequest stand-in for wrap_model_call tests."""
    request = MagicMock()
    request.messages = list(messages)
    request.runtime = runtime
    request.override = lambda **updates: _override_request(request, updates)
    return request


def _override_request(request, updates):
    """Mimic ModelRequest.override(): return a copy with fields replaced."""
    new = MagicMock()
    new.messages = updates.get("messages", request.messages)
    new.runtime = updates.get("runtime", request.runtime)
    new.override = lambda **u: _override_request(new, u)
    return new


def _make_ref(
    task_id: str = "t1",
    thread_id: str = "test-thread",
    status=None,
    subagent_type: str = "general-purpose",
    description: str = "test task",
    result=None,
):
    if status is None:
        status = SubagentStatus.RUNNING
    return SimpleNamespace(
        task_id=task_id,
        thread_id=thread_id,
        trace_id="trace-1",
        subagent_type=subagent_type,
        status=status,
        config=SimpleNamespace(timeout_seconds=300),
        executor=None,
        result=result,
        description=description,
        created_at=datetime.now(),
        idle_since=None,
    )


def _task_call(task_id="call_1"):
    return {"name": "task", "id": task_id, "args": {"prompt": "do something"}}


def _other_call(name="bash", call_id="call_other"):
    return {"name": name, "id": call_id, "args": {}}


def _raw_tool_call(call_id: str, name: str = "task") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


# ---------------------------------------------------------------------------
# Test 1: SubagentStatusMessage.from_refs builds correct content
# ---------------------------------------------------------------------------


class TestSubagentStatusMessageFromRefs:
    def test_status_message_from_refs_running(self):
        """SubagentStatusMessage includes running subagents with task_id, type, description."""
        refs = [_make_ref(task_id="t1", status=SubagentStatus.RUNNING, description="analyze data")]
        msg = SubagentStatusMessage.from_refs(refs)
        content = str(msg.content)
        assert "t1" in content
        assert "general-purpose" in content
        assert "analyze data" in content
        assert "Running" in content

    def test_status_message_from_refs_completed(self):
        """COMPLETED subagents show 'call wait_for_tasks' not result."""
        refs = [
            _make_ref(
                task_id="t2",
                status=SubagentStatus.COMPLETED,
                result=SimpleNamespace(error=None, result="secret result text"),
            )
        ]
        msg = SubagentStatusMessage.from_refs(refs)
        content = str(msg.content)
        assert "t2" in content
        assert "wait_for_tasks" in content
        # Should NOT include the raw result text
        assert "secret result text" not in content

    def test_status_message_from_refs_interrupted(self):
        """INTERRUPTED subagents show 'awaiting user input'."""
        refs = [_make_ref(task_id="t3", status=SubagentStatus.INTERRUPTED)]
        msg = SubagentStatusMessage.from_refs(refs)
        content = str(msg.content)
        assert "t3" in content
        assert "Interrupted" in content
        assert "POST /resume" in content

    def test_status_message_additional_kwargs(self):
        """Message has hide_from_ui, subagent_status, subagents in additional_kwargs."""
        refs = [_make_ref(task_id="t1", status=SubagentStatus.RUNNING)]
        msg = SubagentStatusMessage.from_refs(refs)
        assert msg.additional_kwargs.get("hide_from_ui") is True
        assert msg.additional_kwargs.get("subagent_status") is True
        subagents = msg.additional_kwargs.get("subagents")
        assert isinstance(subagents, list)
        assert len(subagents) == 1
        assert subagents[0]["task_id"] == "t1"


# ---------------------------------------------------------------------------
# Test 2: wrap_model_call injection
# ---------------------------------------------------------------------------


class TestWrapModelCall:
    @pytest.mark.asyncio
    async def test_wrap_model_call_no_subagents_passthrough(self):
        """No subagents -> handler called with original request."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        request = _make_request([HumanMessage(content="hello")], runtime)

        captured: list = []

        async def handler(req):
            captured.append(req)
            return MagicMock()

        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = []
            await mw.awrap_model_call(request, handler)

        assert len(captured) == 1
        # Original request passed through (no override)
        assert captured[0] is request

    @pytest.mark.asyncio
    async def test_wrap_model_call_with_subagents_injects_status(self):
        """With subagents -> handler called with augmented request."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        original_messages = [HumanMessage(content="hello")]
        request = _make_request(original_messages, runtime)

        captured: list = []

        async def handler(req):
            captured.append(req)
            return MagicMock()

        ref = _make_ref(task_id="t1", status=SubagentStatus.RUNNING)
        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = [ref]
            await mw.awrap_model_call(request, handler)

        assert len(captured) == 1
        sent_messages = captured[0].messages
        # Original messages preserved
        assert sent_messages[0] is original_messages[0]
        # Status message appended
        assert len(sent_messages) == 2
        assert isinstance(sent_messages[1], SubagentStatusMessage)


# ---------------------------------------------------------------------------
# Test 3: after_model limit enforcement (from SubagentLimitMiddleware tests)
# ---------------------------------------------------------------------------


class TestAfterModelLimit:
    def test_after_model_truncates_excess_task_calls(self):
        """AIMessage with >max_concurrent task calls -> truncated."""
        mw = SubagentContextMiddleware(max_concurrent=2)
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3"), _task_call("t4")],
        )
        result = mw._enforce_limit(msg)
        assert result is not None
        updated_msg = result["messages"][0]
        task_calls = [tc for tc in updated_msg.tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2
        assert task_calls[0]["id"] == "t1"
        assert task_calls[1]["id"] == "t2"

    def test_after_model_no_truncation_when_within_limit(self):
        """AIMessage with <=max_concurrent task calls -> no change."""
        mw = SubagentContextMiddleware(max_concurrent=3)
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")],
        )
        result = mw._enforce_limit(msg)
        assert result is None

    def test_after_model_non_task_calls_preserved(self):
        """Non-task tool calls (bash, read) survive truncation of excess task calls."""
        mw = SubagentContextMiddleware(max_concurrent=2)
        msg = AIMessage(
            content="",
            tool_calls=[
                _other_call("bash", "b1"),
                _task_call("t1"),
                _task_call("t2"),
                _task_call("t3"),
                _other_call("read", "r1"),
            ],
        )
        result = mw._enforce_limit(msg)
        assert result is not None
        updated_msg = result["messages"][0]
        names = [tc["name"] for tc in updated_msg.tool_calls]
        assert "bash" in names
        assert "read" in names
        task_calls = [tc for tc in updated_msg.tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2

    def test_after_model_truncation_syncs_raw_provider_tool_calls(self):
        """Truncation syncs additional_kwargs['tool_calls'] (provider-level metadata)."""
        mw = SubagentContextMiddleware(max_concurrent=2)
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3"), _task_call("t4")],
            additional_kwargs={
                "tool_calls": [
                    _raw_tool_call("t1"),
                    _raw_tool_call("t2"),
                    _raw_tool_call("t3"),
                    _raw_tool_call("t4"),
                ]
            },
            response_metadata={"finish_reason": "tool_calls"},
        )
        result = mw._enforce_limit(msg)
        assert result is not None
        updated_msg = result["messages"][0]
        assert [tc["id"] for tc in updated_msg.tool_calls] == ["t1", "t2"]
        assert [tc["id"] for tc in updated_msg.additional_kwargs["tool_calls"]] == ["t1", "t2"]
        assert updated_msg.response_metadata["finish_reason"] == "tool_calls"


# ---------------------------------------------------------------------------
# Test 4: after_model guard enforcement (from PendingTaskGuardMiddleware tests)
# ---------------------------------------------------------------------------


class TestAfterModelGuard:
    def test_after_model_guard_injects_reminder_when_running(self):
        """AIMessage with no tool_calls + running subagents -> reminder injected."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        last = AIMessage(content="I'm done", tool_calls=[])
        state = {"messages": [HumanMessage(content="do X"), last]}
        ref = _make_ref(task_id="t1", status=SubagentStatus.RUNNING)
        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = [ref]
            result = mw._enforce_guard(state, runtime, last)
        assert result is not None
        injected = result["messages"][-1]
        assert "wait_for_tasks" in str(injected.content).lower()
        assert "t1" in str(injected.content)

    def test_after_model_guard_forces_tool_choice_after_3(self):
        """3rd reminder -> tool_choice forced to wait_for_tasks."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        last = AIMessage(content="I'm done", tool_calls=[])
        state = {"messages": [HumanMessage(content="do X"), last]}
        ref = _make_ref(task_id="t1", status=SubagentStatus.RUNNING)
        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = [ref]
            mw._enforce_guard(state, runtime, last)  # 1
            mw._enforce_guard(state, runtime, last)  # 2
            result = mw._enforce_guard(state, runtime, last)  # 3
        assert result is not None
        assert result["tool_choice"] == {"type": "tool", "name": "wait_for_tasks"}

    def test_after_model_guard_no_action_when_tool_calls_present(self):
        """AIMessage with tool_calls -> no guard."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        last = AIMessage(content="calling tool", tool_calls=[{"name": "read_file", "args": {}, "id": "c1"}])
        state = {"messages": [HumanMessage(content="do X"), last]}
        ref = _make_ref(task_id="t1", status=SubagentStatus.RUNNING)
        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = [ref]
            result = mw._enforce_guard(state, runtime, last)
        assert result is None

    def test_after_model_guard_no_action_when_no_subagents(self):
        """No running subagents -> no guard."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        last = AIMessage(content="I'm done", tool_calls=[])
        state = {"messages": [HumanMessage(content="do X"), last]}
        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = []
            result = mw._enforce_guard(state, runtime, last)
        assert result is None

    def test_after_model_guard_no_action_when_tasks_terminal_or_stopped(self):
        """COMPLETED/IDLE/INTERRUPTED statuses don't trigger the guard (only PENDING/RUNNING)."""
        mw = SubagentContextMiddleware()
        runtime = _make_runtime()
        last = AIMessage(content="I'm done", tool_calls=[])
        state = {"messages": [HumanMessage(content="do X"), last]}
        refs = [
            _make_ref(task_id="t1", status=SubagentStatus.COMPLETED),
            _make_ref(task_id="t2", status=SubagentStatus.IDLE),
            _make_ref(task_id="t3", status=SubagentStatus.INTERRUPTED),
        ]
        with patch("deerflow.subagents.agent_registry.agent_registry") as mock_reg:
            mock_reg.list_by_thread.return_value = refs
            result = mw._enforce_guard(state, runtime, last)
        assert result is None
