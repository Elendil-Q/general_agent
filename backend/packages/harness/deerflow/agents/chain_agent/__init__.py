"""Chain agent — a YAML-driven DAG of subagent invocations.

Triggered by the ``/chain:<name>`` slash command. The Gateway swaps the
agent factory to :func:`make_chain_agent` for that turn; the chain runs as a
top-level graph (peer to the lead agent), with each node invoking one
subagent from the existing registry to completion.
"""

from deerflow.agents.chain_agent.agent import make_chain_agent
from deerflow.agents.chain_agent.graph import build_chain_graph
from deerflow.agents.chain_agent.state import ChainState

__all__ = [
    "make_chain_agent",
    "build_chain_graph",
    "ChainState",
]
