"""Tests for chain graph construction — nodes + edges from ``depends_on``.

These exercise the graph topology only (no model invocation). A light fake
app_config is used because ``build_chain_graph`` only threads it into the node
closure; the nodes are never invoked here.
"""

from __future__ import annotations

from types import SimpleNamespace

from langgraph.graph import END, START

from deerflow.agents.chain_agent.graph import _terminal_node_names, build_chain_graph
from deerflow.chains.parser import parse_chain_file
from deerflow.chains.types import ChainCategory


def _chain(tmp_path, name: str, body: str):
    f = tmp_path / f"{name}.yaml"
    f.write_text(body, encoding="utf-8")
    return parse_chain_file(f, ChainCategory.PUBLIC)


def _app_config():
    return SimpleNamespace()


def _edges(graph):
    return {(s, t) for s, t in graph.edges}


def test_linear_chain_edges(tmp_path):
    chain = _chain(
        tmp_path,
        "linear",
        """
description: linear
nodes:
  a:
    subagent: general-purpose
  b:
    subagent: general-purpose
    depends_on: [a]
  c:
    subagent: general-purpose
    depends_on: [b]
""",
    )
    g = build_chain_graph(chain, app_config=_app_config())
    edges = _edges(g)
    assert (START, "a") in edges
    assert ("a", "b") in edges
    assert ("b", "c") in edges
    assert ("c", END) in edges


def test_parallel_roots_then_join(tmp_path):
    chain = _chain(
        tmp_path,
        "fanout",
        """
description: fanout
nodes:
  root1:
    subagent: general-purpose
  root2:
    subagent: general-purpose
  join:
    subagent: general-purpose
    depends_on: [root1, root2]
""",
    )
    g = build_chain_graph(chain, app_config=_app_config())
    edges = _edges(g)
    # Both roots edge from START (parallel fan-out)
    assert (START, "root1") in edges
    assert (START, "root2") in edges
    # Join edges from both roots (barrier)
    assert ("root1", "join") in edges
    assert ("root2", "join") in edges
    # Join is terminal -> END
    assert ("join", END) in edges


def test_multiple_terminals_edge_to_end(tmp_path):
    chain = _chain(
        tmp_path,
        "multi-term",
        """
description: multi terminal
nodes:
  a:
    subagent: general-purpose
  b:
    subagent: general-purpose
""",
    )
    g = build_chain_graph(chain, app_config=_app_config())
    edges = _edges(g)
    assert ("a", END) in edges
    assert ("b", END) in edges


def test_terminal_detection():
    from deerflow.chains.types import ChainNode

    nodes = {
        "a": ChainNode(subagent="x", depends_on=[]),
        "b": ChainNode(subagent="x", depends_on=["a"]),
        "c": ChainNode(subagent="x", depends_on=[]),
    }
    # b depends on a; c has no downstream. Terminals = {b, c}.
    assert _terminal_node_names(nodes) == {"b", "c"}


def test_single_node_chain(tmp_path):
    chain = _chain(
        tmp_path,
        "single",
        """
description: single
nodes:
  solo:
    subagent: general-purpose
""",
    )
    g = build_chain_graph(chain, app_config=_app_config())
    edges = _edges(g)
    assert (START, "solo") in edges
    assert ("solo", END) in edges
