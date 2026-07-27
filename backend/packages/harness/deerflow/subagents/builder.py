"""Shared helper for building a subagent's compiled agent.

Extracted from ``SubagentExecutor._create_agent`` so that both the executor
and the chain-pipeline nodes construct subagents from the same code path
(same middleware chain, model resolution, tool policy, ``ThreadState`` schema).
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.tools import BaseTool

from deerflow.agents.thread_state import ThreadState
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.models import create_chat_model
from deerflow.subagents.config import SubagentConfig


def build_subagent_agent(
    config: SubagentConfig,
    tools: list[BaseTool],
    *,
    app_config: AppConfig | None = None,
    model_name: str,
    deferred_setup=None,
    checkpointer=None,
):
    """Build a compiled subagent from a ``SubagentConfig``.

    Mirrors the non-workflow branch of ``SubagentExecutor._create_agent``:
    thinking disabled, no model-level tracing (tracing is attached at the
    graph root), the shared ``build_subagent_runtime_middlewares`` chain, and
    the ``ThreadState`` schema.

    Args:
        config: The subagent configuration. Must NOT have ``workflow`` set —
            workflow subagents own their graph and go through a different path.
        tools: Policy-filtered tools to bind to the agent.
        app_config: Resolved AppConfig. Falls back to ``get_app_config()``.
        model_name: The already-resolved model name. The caller resolves it
            via ``resolve_subagent_model_name`` so the same name can be used
            for both tool assembly (vision-tool gating) and the model itself.
        deferred_setup: Optional deferred-tool setup (lead-agent parity).
        checkpointer: Optional checkpointer. ``None`` for a stateless
            single-invocation subagent (chain-pipeline nodes — they run to
            completion in one ``ainvoke``); the executor passes its own
            ``InMemorySaver`` so subagent interrupt/resume works.
    """
    if config.workflow:
        raise ValueError("build_subagent_agent does not support workflow subagents (config.workflow is set). Use the workflow factory path instead.")

    from deerflow.agents.middlewares.tool_error_handling_middleware import (
        build_subagent_runtime_middlewares,
    )

    resolved_app_config = app_config or get_app_config()
    model = create_chat_model(
        name=model_name,
        thinking_enabled=False,
        app_config=resolved_app_config,
        attach_tracing=False,
    )
    middlewares = build_subagent_runtime_middlewares(
        app_config=resolved_app_config,
        model_name=model_name,
        lazy_init=True,
        deferred_setup=deferred_setup,
        config=config,
    )
    # system_prompt is injected via initial-state messages by callers, to
    # avoid multiple SystemMessages which some LLM APIs don't support.
    return create_agent(
        model=model,
        tools=tools,
        middleware=middlewares,
        system_prompt=None,
        state_schema=ThreadState,
        checkpointer=checkpointer,
    )
