"""Chain agent factory.

A chain is a top-level graph (peer to the lead agent) for the turn it is
invoked via ``/chain:<name>``. The Gateway's ``start_run`` swaps the agent
factory from ``make_lead_agent`` to ``make_chain_agent`` when the user
message carries the ``/chain:`` prefix (see
``backend/app/gateway/services.py``).

The chain graph is rebuilt per-run (the worker calls this factory fresh each
turn) and the worker attaches the thread checkpointer after construction, so
``interrupt()`` inside a node (or a node's subagent) gets native HITL — the
chain IS the top-level graph for that turn, not a subgraph.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from deerflow.agents.chain_agent.graph import build_chain_graph
from deerflow.config.app_config import AppConfig, get_app_config

logger = logging.getLogger(__name__)


def _get_runtime_config(config: RunnableConfig) -> dict:
    """Merge ``configurable`` with LangGraph runtime ``context`` (mirrors lead agent)."""
    cfg = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        cfg.update(context)
    return cfg


def make_chain_agent(config: RunnableConfig, *, app_config: AppConfig | None = None) -> CompiledStateGraph:
    """Build the chain graph for the turn.

    Reads ``chain_name`` from the runtime config (injected by the Gateway's
    ``build_run_config`` - the same mechanism as ``agent_name``), loads the
    chain spec from the auto-discovered registry, and compiles its
    ``StateGraph``.

    The checkpointer is intentionally NOT passed here - the run worker attaches
    the shared per-thread checkpointer after construction (see
    ``runtime/runs/worker.py``), exactly as it does for ``make_lead_agent``.

    Resume: when the Gateway injects a ``chain_resume`` mapping (a
    ``/chain-resume:<name>`` run), it carries ``completed_nodes`` (node name
    -> cached result) and ``input`` (the original user input). This factory
    instantiates a :class:`ChainProgressStore` for the (thread, chain, user)
    triple and forwards both to :func:`build_chain_graph` so completed nodes
    short-circuit and per-node progress is recorded for the resumed run.
    """
    resolved_app_config = app_config or get_app_config()
    cfg = _get_runtime_config(config)
    chain_name = cfg.get("chain_name")
    if not chain_name:
        raise ValueError("make_chain_agent called without a 'chain_name' in the runtime config.")

    thread_id = cfg.get("thread_id")
    run_id = cfg.get("run_id")
    chain_resume = cfg.get("chain_resume")
    user_id = cfg.get("user_id")

    from deerflow.chains.storage.chain_storage import get_or_new_chain_storage

    storage = get_or_new_chain_storage(app_config=resolved_app_config)
    chain = storage.load_chain(chain_name)
    if chain is None:
        raise ValueError(f"Unknown chain {chain_name!r}. Available chains: {[c.name for c in storage.load_chains()]}")

    completed_nodes: dict[str, str] | None = None
    resume_input: str | None = None
    progress_store = None
    if isinstance(chain_resume, dict):
        completed_nodes = dict(chain_resume.get("completed_nodes") or {})
        resume_input = chain_resume.get("input") or None

    # Instantiate the progress store so node runners can record start/finish.
    # Only meaningful for a real run (thread_id + run_id present); tests and
    # ad-hoc invocations leave it as None.
    if thread_id and run_id:
        try:
            from deerflow.chains.progress import ChainProgressStore

            progress_store = ChainProgressStore(thread_id, chain_name, user_id=user_id)
        except Exception:
            logger.warning("Failed to build ChainProgressStore for %r", chain_name, exc_info=True)
            progress_store = None

    logger.info(
        "Building chain graph %r (%d nodes, resume=%s)",
        chain.name,
        len(chain.nodes),
        bool(completed_nodes),
    )
    graph = build_chain_graph(
        chain,
        app_config=resolved_app_config,
        completed_nodes=completed_nodes,
        resume_input=resume_input,
        progress_store=progress_store,
        run_id=run_id,
    )
    return graph.compile()
