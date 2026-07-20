"""Integration test for the chain node runner.

Mocks the heavy subagent-building deps so we can validate the node runner's
real behaviour end-to-end: prompt rendering ({input}, {node_outputs.<name>}),
state threading, and terminal-node AIMessage appending — without a real LLM.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.chain_agent import graph as graph_module
from deerflow.agents.chain_agent import nodes as nodes_module


def _fake_subagent_config(name: str):
    cfg = SimpleNamespace()
    cfg.name = name
    cfg.model = "inherit"
    cfg.tools = None
    cfg.disallowed_tools = ["task"]
    cfg.skills = None
    cfg.workflow = None
    cfg.max_turns = 10
    cfg.timeout_seconds = 60
    cfg.system_prompt = None
    return cfg


@pytest.fixture
def patched_runner(monkeypatch):
    """Patch the node runner's heavy deps with a prompt-driven fake agent."""
    # Every ainvoke call's prompt is recorded here, in execution order.
    seen_prompts: list[str] = []

    def fake_get_subagent_config(name, *, app_config=None):
        return _fake_subagent_config(name)

    def fake_resolve_subagent_model_name(config, parent_model, *, app_config=None):
        return "fake-model"

    def fake_get_available_tools(**kwargs):
        return []

    class _FakeAgent:
        async def ainvoke(self, state, config=None):
            prompt = state["messages"][0].content
            seen_prompts.append(prompt)
            # Respond based on the prompt prefix so each node gets a distinct
            # canned answer even though both reference subagent "general-purpose".
            if prompt.startswith("research:"):
                text = "RESEARCH-RESULT"
            elif prompt.startswith("report from:"):
                text = "REPORT-RESULT"
            else:
                text = "GENERIC-RESULT"
            return {"messages": [AIMessage(content=text)]}

    def fake_build_subagent_agent(config, tools, *, app_config, model_name, deferred_setup=None, checkpointer=None):
        return _FakeAgent()

    monkeypatch.setattr(nodes_module, "get_subagent_config", fake_get_subagent_config)
    monkeypatch.setattr(nodes_module, "resolve_subagent_model_name", fake_resolve_subagent_model_name)
    monkeypatch.setattr(nodes_module, "build_subagent_agent", fake_build_subagent_agent)

    # The lazy `from deerflow.tools import get_available_tools` inside run_node
    # reads deerflow.tools.get_available_tools at call time, so patch it there.
    import deerflow.tools as tools_module

    monkeypatch.setattr(tools_module, "get_available_tools", fake_get_available_tools)

    return SimpleNamespace(seen_prompts=seen_prompts)


@pytest.mark.anyio
async def test_two_node_chain_threads_input_and_upstream_output(tmp_path, patched_runner):
    """A linear chain (researcher → reporter) threads {input} into the first
    node and {node_outputs.researcher} into the second; the terminal node
    appends its AIMessage to messages."""
    chain_yaml = """
description: two-node linear
nodes:
  researcher:
    subagent: general-purpose
    prompt: "research: {input}"
  reporter:
    subagent: general-purpose
    depends_on: [researcher]
    prompt: "report from: {node_outputs.researcher}"
"""
    f = tmp_path / "linear.yaml"
    f.write_text(chain_yaml, encoding="utf-8")
    from deerflow.chains.parser import parse_chain_file
    from deerflow.chains.types import ChainCategory

    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None

    graph = graph_module.build_chain_graph(chain, app_config=SimpleNamespace())
    compiled = graph.compile()

    result = await compiled.ainvoke(
        {"messages": [HumanMessage(content="hello world")]},
        config={"configurable": {"thread_id": "t1", "model_name": "fake-model"}, "context": {}},
    )

    # Node prompts were rendered with {input} and {node_outputs.<name>} substituted.
    assert patched_runner.seen_prompts == ["research: hello world", "report from: RESEARCH-RESULT"]

    # The terminal node's AIMessage was appended to messages (streams + persists).
    final_texts = [m.content for m in result["messages"] if isinstance(m, AIMessage)]
    assert "REPORT-RESULT" in final_texts

    # node_outputs carries every node's result.
    assert result["node_outputs"]["researcher"] == "RESEARCH-RESULT"
    assert result["node_outputs"]["reporter"] == "REPORT-RESULT"


@pytest.mark.anyio
async def test_default_prompt_when_node_has_no_prompt(tmp_path, patched_runner):
    """A node with no prompt template gets a default built from input + upstream."""
    chain_yaml = """
description: default prompt
nodes:
  a:
    subagent: general-purpose
  b:
    subagent: general-purpose
    depends_on: [a]
"""
    f = tmp_path / "default.yaml"
    f.write_text(chain_yaml, encoding="utf-8")
    from deerflow.chains.parser import parse_chain_file
    from deerflow.chains.types import ChainCategory

    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    graph = graph_module.build_chain_graph(chain, app_config=SimpleNamespace())
    compiled = graph.compile()

    result = await compiled.ainvoke(
        {"messages": [HumanMessage(content="the task")]},
        config={"configurable": {"thread_id": "t1"}, "context": {}},
    )

    # Node a (root, no upstream): default prompt is just the task.
    assert patched_runner.seen_prompts[0].startswith("Task:\nthe task")
    # Node b (depends on a): default prompt includes upstream result.
    assert "[a]" in patched_runner.seen_prompts[1]
    assert "GENERIC-RESULT" in patched_runner.seen_prompts[1]
    # node_outputs populated for both.
    assert set(result["node_outputs"]) == {"a", "b"}


@pytest.mark.anyio
async def test_resume_skips_completed_nodes(tmp_path, patched_runner):
    """``completed_nodes`` short-circuits nodes that already finished."""
    chain_yaml = """
description: skip chain
nodes:
  researcher:
    subagent: general-purpose
    prompt: "research: {input}"
  reporter:
    subagent: general-purpose
    depends_on: [researcher]
    prompt: "report from: {node_outputs.researcher}"
"""
    f = tmp_path / "skip.yaml"
    f.write_text(chain_yaml, encoding="utf-8")
    from deerflow.chains.parser import parse_chain_file
    from deerflow.chains.types import ChainCategory

    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    completed = {"researcher": "CACHED-RESEARCH"}
    graph = graph_module.build_chain_graph(
        chain,
        app_config=SimpleNamespace(),
        completed_nodes=completed,
        resume_input="the original task",
    )
    compiled = graph.compile()

    result = await compiled.ainvoke(
        {"messages": []},
        config={"configurable": {"thread_id": "t1"}, "context": {}},
    )

    # The researcher node was skipped (cached), only the reporter ran.
    assert patched_runner.seen_prompts == ["report from: CACHED-RESEARCH"]
    assert result["node_outputs"]["researcher"] == "CACHED-RESEARCH"
    assert result["node_outputs"]["reporter"] == "REPORT-RESULT"
    # The terminal reporter still appends its AIMessage.
    assert any(m.content == "REPORT-RESULT" for m in result["messages"] if isinstance(m, AIMessage))


class _RecordingProgressStore:
    """Minimal progress store stub recording method calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    def mark_node_running(self, run_id, node_name):
        self.calls.append(("mark_node_running", run_id, node_name))

    def update_node(self, run_id, node_name, result, *, status="completed"):
        self.calls.append(("update_node", run_id, node_name, result, status))


@pytest.mark.anyio
async def test_progress_store_writes_per_node(tmp_path, patched_runner):
    """``progress_store`` + ``run_id`` drive mark_node_running / update_node."""
    chain_yaml = """
description: progress chain
nodes:
  a:
    subagent: general-purpose
  b:
    subagent: general-purpose
    depends_on: [a]
"""
    f = tmp_path / "progress.yaml"
    f.write_text(chain_yaml, encoding="utf-8")
    from deerflow.chains.parser import parse_chain_file
    from deerflow.chains.types import ChainCategory

    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    progress = _RecordingProgressStore()
    graph = graph_module.build_chain_graph(
        chain,
        app_config=SimpleNamespace(),
        progress_store=progress,
        run_id="run-xyz",
    )
    compiled = graph.compile()

    await compiled.ainvoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t1"}, "context": {}},
    )

    running = [c for c in progress.calls if c[0] == "mark_node_running"]
    updates = [c for c in progress.calls if c[0] == "update_node"]
    assert {c[2] for c in running} == {"a", "b"}
    assert {c[1] for c in updates} == {"run-xyz"}
    assert {c[2] for c in updates} == {"a", "b"}
    assert all(c[3] for c in updates)  # non-empty result text
