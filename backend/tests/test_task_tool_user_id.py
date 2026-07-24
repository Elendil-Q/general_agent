"""Task 4: the runtime user_id is threaded into the subagent registry calls.

Spec success criterion #2: a per-user subagent type created at runtime must be
immediately usable via the ``task`` tool, with no restart. That requires the
task tool to resolve the effective ``user_id`` (from ``runtime.context``, with a
contextvar fallback) and forward it to ``get_available_subagent_names`` and
``get_subagent_config`` so the per-user storage layer is consulted.
"""

import asyncio
import importlib
from enum import Enum
from types import SimpleNamespace

from deerflow.runtime.user_context import reset_current_user, set_current_user
from deerflow.subagents.config import SubagentConfig

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


class FakeSubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"

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
        return self.is_terminal or self is type(self).INTERRUPTED


def _make_runtime(*, app_config=None, user_id=None) -> SimpleNamespace:
    context: dict = {"thread_id": "thread-1"}
    if app_config is not None:
        context["app_config"] = app_config
    if user_id is not None:
        context["user_id"] = user_id
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context=context,
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )


def _make_subagent_config(name: str = "general-purpose") -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
    )


def _make_result(result: str) -> SimpleNamespace:
    return SimpleNamespace(
        status=FakeSubagentStatus.COMPLETED,
        ai_messages=[],
        result=result,
        error=None,
        token_usage_records=[],
        usage_reported=False,
        interrupts=None,
        subagent_thread_id=None,
    )


def _run_task_tool(**kwargs) -> str:
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


async def _no_sleep(_: float) -> None:
    return None


def _wire_completed_subagent(monkeypatch, captured):
    """Patch executor + polling so task_tool completes synchronously."""

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: _make_result("done"))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _e: None)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kw: ["tool-a"])


def _make_registry_fakes(captured, config):
    def fake_get_available_subagent_names(*, user_id=None, app_config=None):
        captured["names_user_id"] = user_id
        captured["names_app_config"] = app_config
        return ["general-purpose"]

    def fake_get_subagent_config(name, *, user_id=None, app_config=None):
        captured["config_user_id"] = user_id
        captured["config_app_config"] = app_config
        captured["config_name"] = name
        return config

    return fake_get_available_subagent_names, fake_get_subagent_config


def test_task_tool_forwards_runtime_context_user_id(monkeypatch):
    """runtime.context['user_id'] must reach both registry calls."""
    captured: dict = {}
    config = _make_subagent_config()
    names_fake, config_fake = _make_registry_fakes(captured, config)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", names_fake)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", config_fake)
    _wire_completed_subagent(monkeypatch, captured)

    output = _run_task_tool(
        runtime=_make_runtime(user_id="runtime-user-42"),
        description="delegate work",
        prompt="do something",
        subagent_type="general-purpose",
        tool_call_id="tc-user-id",
    )

    assert output == "Task Succeeded. Result: done"
    assert captured["names_user_id"] == "runtime-user-42"
    assert captured["config_user_id"] == "runtime-user-42"
    assert captured["config_name"] == "general-purpose"


def test_task_tool_forwards_user_id_alongside_app_config(monkeypatch):
    """user_id must be forwarded even on the app_config branch."""
    captured: dict = {}
    app_config = object()
    config = _make_subagent_config()
    names_fake, config_fake = _make_registry_fakes(captured, config)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", names_fake)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", config_fake)
    _wire_completed_subagent(monkeypatch, captured)

    output = _run_task_tool(
        runtime=_make_runtime(app_config=app_config, user_id="runtime-user-42"),
        description="delegate work",
        prompt="do something",
        subagent_type="general-purpose",
        tool_call_id="tc-user-id",
    )

    assert output == "Task Succeeded. Result: done"
    assert captured["names_user_id"] == "runtime-user-42"
    assert captured["config_user_id"] == "runtime-user-42"
    assert captured["names_app_config"] is app_config
    assert captured["config_app_config"] is app_config


def test_task_tool_falls_back_to_contextvar_user_id(monkeypatch):
    """Without runtime.context['user_id'], resolve from the contextvar."""
    captured: dict = {}
    config = _make_subagent_config()
    names_fake, config_fake = _make_registry_fakes(captured, config)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", names_fake)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", config_fake)
    _wire_completed_subagent(monkeypatch, captured)

    runtime = _make_runtime()
    token = set_current_user(SimpleNamespace(id="ctxvar-user-7"))
    try:
        output = _run_task_tool(
            runtime=runtime,
            description="delegate work",
            prompt="do something",
            subagent_type="general-purpose",
            tool_call_id="tc-user-id",
        )
    finally:
        reset_current_user(token)

    assert output == "Task Succeeded. Result: done"
    assert captured["names_user_id"] == "ctxvar-user-7"
    assert captured["config_user_id"] == "ctxvar-user-7"
