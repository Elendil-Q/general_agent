"""Chain agent state schema.

A chain runs as a top-level graph (peer to the lead agent) for the turn it is
invoked. It shares the thread + ``ThreadState`` messages with the lead agent so
the chain's final result (appended to ``messages``) is visible to subsequent
turns. ``node_outputs`` carries each node's subagent result between nodes and
is ``NotRequired`` so the lead agent's ``ThreadState`` ignores it on later
turns.
"""

from typing import NotRequired

from deerflow.agents.thread_state import ThreadState


class ChainState(ThreadState):
    """Workflow state for a chain pipeline.

    Extends ``ThreadState`` (messages/sandbox/thread_data/...) so the
    checkpointer's per-thread state round-trips across a ``/chain`` turn and
    later normal turns, and adds ``node_outputs`` for inter-node result
    passing inside a single chain run.
    """

    # node name -> final subagent result text
    node_outputs: NotRequired[dict[str, str] | None]
