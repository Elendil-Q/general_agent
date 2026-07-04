"""Verify a workflow subagent can call file/sandbox tools via ToolNode.

Proves the end-to-end path that matters for ``config.workflow`` subagents: a
LangGraph ``StateGraph`` workflow (built by a factory matching the
workflow-subagent contract) that routes the model's ``tool_calls`` through a
``ToolNode`` executes real DeerFlow sandbox tools (``write_file``/``read_file``)
with the full ``ToolRuntime`` — ``sandbox``/``thread_data`` from the graph state
and ``thread_id``/``app_config`` from the runtime context — exactly what
``SubagentExecutor._aexecute`` feeds to ``astream``.

Uses a real ``LocalSandbox`` pointed at a temp workspace (same pattern as
``test_read_file_tool_binary.py``): ``ensure_sandbox_initialized`` is patched to
return it so no provider/``config.yaml`` is needed.
"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import read_file_tool, write_file_tool


def _local_thread_data(tmp_path: Path) -> dict:
    for sub in ("workspace", "uploads", "outputs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "outputs_path": str(tmp_path / "outputs"),
    }


@pytest.fixture
def _local_sandbox(tmp_path, monkeypatch):
    """Patch the sandbox init helpers to use a real LocalSandbox on tmp_path."""
    thread_data = _local_thread_data(tmp_path)
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    return tmp_path, thread_data


class _FakeToolModel:
    """Emits a canned tool_call on the first ``ainvoke``, then a summary.

    ``call_factory`` builds the per-call response so each test can drive a
    different tool (write_file / read_file).
    """

    def __init__(self, call_factory):
        self._call_factory = call_factory
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        return self._call_factory(self.calls)


async def _run_to_interrupt(app, state, context):
    """Run the workflow until it pauses at the approve interrupt."""
    final = None
    async for chunk in app.astream(state, context=context, stream_mode="values"):
        final = chunk
    return final


@pytest.mark.anyio
async def test_workflow_calls_sandbox_write_file_via_toolnode(_local_sandbox):
    tmp_path, thread_data = _local_sandbox

    def factory(call):
        if call == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "/mnt/user-data/workspace/captured.txt", "content": "workflow-wrote-this"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="research summary", id=f"m{call}")

    from deerflow.workflows.research_review import build_graph

    model = _FakeToolModel(factory)
    app = build_graph(model=model, tools=[write_file_tool, read_file_tool], config=None).compile()

    state = {
        "messages": [HumanMessage(content="inspect the workspace and summarize")],
        "sandbox": {"sandbox_id": "local:t1"},
        "thread_data": thread_data,
    }
    context = {"thread_id": "t1", "app_config": None, "user_id": "u1", "is_subagent": True}

    final = await _run_to_interrupt(app, state, context)

    # The sandbox write_file tool executed through the workflow's ToolNode and
    # wrote to the workspace (virtual path resolved via thread_data).
    assert (tmp_path / "workspace" / "captured.txt").read_text(encoding="utf-8") == "workflow-wrote-this"
    # The model produced its tool-free research summary after the tool call.
    assert any(getattr(m, "content", "") == "research summary" for m in final["messages"])
    # Execution reached the approve interrupt (workflow paused, not ended clean).
    assert final.get("__interrupt__")


@pytest.mark.anyio
async def test_workflow_calls_sandbox_read_file_via_toolnode(_local_sandbox):
    tmp_path, thread_data = _local_sandbox
    # Pre-create a workspace file the model will read.
    (tmp_path / "workspace" / "notes.md").write_text("workspace-secret-42", encoding="utf-8")

    def factory(call):
        if call == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "/mnt/user-data/workspace/notes.md"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="research summary", id=f"m{call}")

    from deerflow.workflows.research_review import build_graph

    model = _FakeToolModel(factory)
    app = build_graph(model=model, tools=[write_file_tool, read_file_tool], config=None).compile()

    state = {
        "messages": [HumanMessage(content="read workspace notes and summarize")],
        "sandbox": {"sandbox_id": "local:t1"},
        "thread_data": thread_data,
    }
    context = {"thread_id": "t1", "app_config": None, "user_id": "u1", "is_subagent": True}

    final = await _run_to_interrupt(app, state, context)

    # The sandbox read_file tool executed through the workflow's ToolNode and
    # surfaced the real workspace file content as a ToolMessage.
    tool_messages = [m for m in final["messages"] if m.type == "tool"]
    assert tool_messages, "expected a ToolMessage from read_file"
    assert "workspace-secret-42" in tool_messages[-1].content
    assert final.get("__interrupt__")
