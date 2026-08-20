"""DeerFlow example subagent workflows."""

from deerflow.examples.langgraph.cyber_ops import build_graph as build_cyber_ops_graph
from deerflow.examples.langgraph.parallel_research import build_graph as build_parallel_research_graph
from deerflow.examples.langgraph.research_approval import build_graph as build_research_approval_graph

__all__ = [
    "build_cyber_ops_graph",
    "build_parallel_research_graph",
    "build_research_approval_graph",
]
