"""Tests for the fire-and-forget follow_up tool.

The tool validates the IDLE preconditions, cancels the TTL, registers the
SSE writer, kicks ``executor.continue_async``, and returns immediately —
``wait_for_tasks`` is the blocking collector.
"""

import asyncio
import importlib
import sys
from datetime import datetime
from enum import Enum
from types import SimpleNamespace

from deerflow.subagents.config import SubagentConfig

follow_up_module = importlib.import_module("deerflow.tools.builtins.follow_up")


class FakeSubagentStatus(Enum):
    # Match production enum values so branch comparisons behave identically.
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    IDLE = "idle"

    @property
    def is_terminal(self) -> bool:
        return self in {
            type(self).COMPLETED,
            type(self).FAILED,
            type(self).CANCELLED,
            type(self).TIMED_OUT,
        }

    @property
    def is_stopped(self) -> bool:
        return self.is_terminal or self in {
            type(self).INTERRUPTED,
            type(self).IDLE,
        }


def _make_runtime(thread_id="thread-1") -> SimpleNamespace:
    return SimpleNamespace(context={"thread_id": thread_id})


def _make_config() -> SubagentConfig:
    return SubagentConfig(
        name="general-purpose",
        description="test",
        system_prompt="test",
        max_turns=50,
        timeout_seconds=10,
        keep_alive=True,
    )


class _FakeExecutor:
    def __init__(self):
        self.continue_async_calls: list[tuple[str, str]] = []
        self.continue_with_prompt_calls: list[tuple[str, str]] = []

    def continue_async(self, prompt: str, task_id: str) -> str:
        self.continue_async_calls.append((prompt, task_id))
        return task_id

    def continue_with_prompt(self, prompt: str, task_id: str):
        self.continue_with_prompt_calls.append((prompt, task_id))
        raise AssertionError("follow_up must not block on continue_with_prompt")


def _clean_registry():
    from deerflow.subagents.agent_registry import agent_registry

    for ref in list(agent_registry.list_all()):
        agent_registry.remove(ref.task_id)


def _register(task_id: str, status: FakeSubagentStatus, executor=None):
    from deerflow.subagents.agent_registry import AgentRef, agent_registry

    agent_registry.register(
        AgentRef(
            task_id=task_id,
            thread_id="thread-1",
            trace_id="trace-1",
            subagent_type="general-purpose",
            status=status,
            config=_make_config(),
            executor=executor,
            result=None,
            description="test task",
            created_at=datetime.now(),
        )
    )


def _wire_mocks(monkeypatch):
    executor_mock = sys.modules.get("deerflow.subagents.executor")
    if executor_mock is not None:
        monkeypatch.setattr(executor_mock, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(follow_up_module, "get_stream_writer", lambda: lambda _e: None)


def _run_follow_up(**kwargs) -> str:
    coroutine = getattr(follow_up_module.follow_up, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return follow_up_module.follow_up.func(**kwargs)


def test_follow_up_is_fire_and_forget(monkeypatch):
    """IDLE subagent -> continue_async kicked, returns immediately with a
    pointer to wait_for_tasks (never the blocking continue_with_prompt)."""
    _clean_registry()
    try:
        fake = _FakeExecutor()
        _register("fu-idle", FakeSubagentStatus.IDLE, executor=fake)
        _wire_mocks(monkeypatch)

        from deerflow.subagents.lifecycle import lifecycle_manager

        cancelled: list[str] = []
        monkeypatch.setattr(lifecycle_manager, "cancel_ttl", lambda tid: cancelled.append(tid))

        out = _run_follow_up(
            task_id="fu-idle",
            prompt="do more",
            tool_call_id="tc-fu-1",
            runtime=_make_runtime(),
        )

        assert fake.continue_async_calls == [("do more", "fu-idle")]
        assert fake.continue_with_prompt_calls == []
        assert cancelled == ["fu-idle"]
        assert "wait_for_tasks" in out
        assert "fu-idle" in out
        assert "started" in out.lower()
    finally:
        _clean_registry()


def test_follow_up_rejects_non_idle(monkeypatch):
    _clean_registry()
    try:
        fake = _FakeExecutor()
        _register("fu-running", FakeSubagentStatus.RUNNING, executor=fake)
        _wire_mocks(monkeypatch)

        out = _run_follow_up(
            task_id="fu-running",
            prompt="do more",
            tool_call_id="tc-fu-2",
            runtime=_make_runtime(),
        )

        assert out.startswith("Error:")
        assert "not idle" in out
        assert "running" in out
        assert fake.continue_async_calls == []
    finally:
        _clean_registry()


def test_follow_up_rejects_unknown_task(monkeypatch):
    _clean_registry()
    try:
        _wire_mocks(monkeypatch)

        out = _run_follow_up(
            task_id="fu-missing",
            prompt="do more",
            tool_call_id="tc-fu-3",
            runtime=_make_runtime(),
        )

        assert out.startswith("Error:")
        assert "not idle" in out
    finally:
        _clean_registry()


def test_follow_up_rejects_missing_executor(monkeypatch):
    _clean_registry()
    try:
        _register("fu-noexec", FakeSubagentStatus.IDLE, executor=None)
        _wire_mocks(monkeypatch)

        out = _run_follow_up(
            task_id="fu-noexec",
            prompt="do more",
            tool_call_id="tc-fu-4",
            runtime=_make_runtime(),
        )

        assert out.startswith("Error:")
        assert "no live executor" in out
    finally:
        _clean_registry()


def test_follow_up_docstring_directs_to_wait_for_tasks():
    doc = follow_up_module.follow_up.description
    assert "wait_for_tasks" in doc
    assert "fire-and-forget" in doc.lower() or "background" in doc.lower()
