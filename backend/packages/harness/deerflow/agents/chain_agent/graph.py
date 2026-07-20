"""Build a chain's LangGraph ``StateGraph`` from a parsed ``Chain`` spec.

The ``depends_on`` field maps directly to graph edges - LangGraph's Pregel
engine handles parallel fan-out (multiple roots from ``START``) and
barrier/join (a node with multiple ``depends_on`` waits for all of them)
natively, so no topological sort or ``asyncio.gather`` is needed.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from deerflow.agents.chain_agent.nodes import make_subagent_node
from deerflow.agents.chain_agent.state import ChainState
from deerflow.chains.types import Chain, ChainNode
from deerflow.config.app_config import AppConfig


def _terminal_node_names(nodes: dict[str, ChainNode]) -> set[str]:
    """Nodes that no other node depends on - they edge to ``END``.

    Their result is also appended as a final ``AIMessage`` (see
    ``make_subagent_node``) so the chain's answer persists to the thread.
    """
    has_downstream: set[str] = set()
    for node in nodes.values():
        has_downstream.update(node.depends_on)
    return set(nodes) - has_downstream


def build_chain_graph(
    chain: Chain,
    *,
    app_config: AppConfig,
    completed_nodes: dict[str, str] | None = None,
    resume_input: str | None = None,
    progress_store: Any = None,
    run_id: str | None = None,
) -> StateGraph:
    """Build the uncompiled chain graph.

    Each node runs one subagent to completion. Edges are derived from
    ``depends_on``: a root node (empty ``depends_on``) edges from ``START``;
    a node with ``depends_on: [a, b]`` edges from both ``a`` and ``b`` (a
    barrier - it runs only after both complete); a terminal node edges to
    ``END``.

    The optional ``completed_nodes`` / ``resume_input`` / ``progress_store`` /
    ``run_id`` args wire up ``/chain-resume:`` recovery: nodes whose name is
    present in ``completed_nodes`` short-circuit and replay their cached
    result instead of re-invoking the subagent.
    """
    graph = StateGraph(ChainState)
    terminals = _terminal_node_names(chain.nodes)

    for name, node in chain.nodes.items():
        graph.add_node(
            name,
            make_subagent_node(
                name,
                node,
                app_config=app_config,
                is_terminal=name in terminals,
                completed_nodes=completed_nodes,
                resume_input=resume_input,
                progress_store=progress_store,
                run_id=run_id,
            ),
        )

    for name, node in chain.nodes.items():
        if node.depends_on:
            for dep in node.depends_on:
                graph.add_edge(dep, name)
        else:
            graph.add_edge(START, name)
        if name in terminals:
            graph.add_edge(name, END)

    return graph
