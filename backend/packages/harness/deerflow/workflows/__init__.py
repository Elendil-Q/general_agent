"""Example LangGraph-workflow subagents.

A workflow subagent is registered exactly like a create_agent subagent
(``subagents.custom_agents`` in config.yaml, or ``BUILTIN_SUBAGENTS``), but its
``workflow`` field points to a factory callable via a ``"module.path:object"``
ref — the same loader used for ``config.tools[].use``.

The factory signature is::

    build_graph(*, model, tools, config) -> StateGraph

It must return an *uncompiled* ``StateGraph`` over a ``ThreadState``-compatible
schema (the executor attaches its own checkpointer). See ``backend/AGENTS.md``
for the full contract.
"""

from deerflow.workflows.research_review import build_graph as build_research_review_graph

__all__ = ["build_research_review_graph"]
