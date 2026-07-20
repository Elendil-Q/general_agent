"""Chain agent state schema.

A chain runs as a top-level graph (peer to the lead agent) for the turn it is
invoked. It shares the thread + ``ThreadState`` messages with the lead agent so
the chain's final result (appended to ``messages``) is visible to subsequent
turns. ``node_outputs`` carries each node's subagent result between nodes and
is ``NotRequired`` so the lead agent's ``ThreadState`` ignores it on later
turns.
"""

from typing import Annotated, NotRequired

from deerflow.agents.thread_state import ThreadState


def merge_node_outputs(
    existing: dict[str, str] | None,
    new: dict[str, str] | None,
) -> dict[str, str] | None:
    """Reducer for the inter-node ``node_outputs`` channel.

    Without a reducer, LangGraph's default "last write wins" semantics would
    make parallel root nodes clobber each other's results - each root returns
    the full ``node_outputs`` dict it read, and only one survives. With this
    reducer, nodes return *incremental* updates (``{node_name: result}``) that
    are merged into the running dict.

    An empty dict (``{}``) is treated as an explicit "clear" so a fresh chain
    run can wipe stale ``node_outputs`` left in the checkpoint from a previous
    turn (the chain graph sets ``node_outputs={}`` in its graph input).
    """
    if new is None:
        return existing
    if not new:
        return {}
    if existing is None:
        return dict(new)
    merged = dict(existing)
    merged.update(new)
    return merged


class ChainState(ThreadState):
    """Workflow state for a chain pipeline.

    Extends ``ThreadState`` (messages/sandbox/thread_data/...) so the
    checkpointer's per-thread state round-trips across a ``/chain`` turn and
    later normal turns, and adds ``node_outputs`` for inter-node result
    passing inside a single chain run.
    """

    # node name -> final subagent result text
    node_outputs: Annotated[NotRequired[dict[str, str] | None], merge_node_outputs]
