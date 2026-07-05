"""Chain graph node runner — each node invokes one subagent to completion.

This is the "方案 2 inline subagent" path: a node resolves a subagent from the
existing registry, builds it via the shared ``build_subagent_agent`` helper
(same middleware chain / tool policy as a ``task``-tool subagent), and runs it
to completion with ``ainvoke``. No background executor, no polling — the node
is an async coroutine on the chain graph's own event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from deerflow.agents.chain_agent.state import ChainState
from deerflow.chains.types import ChainNode
from deerflow.config.app_config import AppConfig
from deerflow.subagents.builder import build_subagent_agent
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.registry import get_subagent_config

logger = logging.getLogger(__name__)


def _filter_tools(all_tools, allowed, disallowed):
    """Apply a subagent's tool allowlist/denylist (mirrors SubagentExecutor._filter_tools)."""
    filtered = list(all_tools)
    if allowed is not None:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.name in allowed_set]
    if disallowed is not None:
        disallowed_set = set(disallowed)
        filtered = [t for t in filtered if t.name not in disallowed_set]
    return filtered


def _extract_text(message: Any) -> str:
    """Best-effort extraction of a message's text content (str or content blocks)."""
    if message is None:
        return ""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _extract_user_input(state: ChainState) -> str:
    """The user's original message for this chain run.

    Nodes never append to ``messages`` (only the terminal node does, at the
    very end), so the last message in state is reliably the user's input
    throughout the chain run. Falls back to the first message if needed.
    """
    messages = state.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    text = _extract_text(last)
    if text:
        return text
    return _extract_text(messages[0])


def _render_prompt(node: ChainNode, user_input: str, node_outputs: dict[str, str]) -> str:
    """Render the node's prompt template, or build a default prompt.

    Supports ``{input}`` (the user's original message) and
    ``{node_outputs.<name>}`` placeholders referencing upstream results.
    When the node declares no prompt, a default prompt is assembled from the
    user input plus the upstream node outputs.
    """
    if node.prompt:
        prompt = node.prompt
        prompt = prompt.replace("{input}", user_input)
        for dep_name in node.depends_on:
            prompt = prompt.replace(
                f"{{node_outputs.{dep_name}}}",
                node_outputs.get(dep_name, ""),
            )
        return prompt

    # Default prompt when none is declared.
    parts = [f"Task:\n{user_input}"]
    if node.depends_on:
        parts.append("\nUpstream results:")
        for dep in node.depends_on:
            parts.append(f"\n[{dep}]\n{node_outputs.get(dep, '')}")
    return "\n".join(parts)


def _extract_final_text(final_state: dict) -> str:
    """Extract the final AIMessage text from the subagent's completed state."""
    messages = final_state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, BaseMessage) and getattr(msg, "type", None) == "ai" or isinstance(msg, BaseMessage) and msg.__class__.__name__ == "AIMessage":
            text = _extract_text(msg)
            if text:
                return text
    # Fallback: last message's text
    if messages:
        return _extract_text(messages[-1])
    return ""


def make_subagent_node(node_name: str, node: ChainNode, *, app_config: AppConfig, is_terminal: bool = False):
    """Build an async graph node that runs one subagent to completion.

    The node:
      1. resolves the subagent config by name from the existing registry
      2. assembles + policy-filters tools (``subagent_enabled=False`` — no
         recursive ``task`` tool, same as ``task``-tool subagents)
      3. builds the agent via the shared ``build_subagent_agent`` helper
      4. renders the prompt (``{input}`` + upstream ``{node_outputs.<name>}``)
      5. seeds ``sandbox``/``thread_data`` from the chain state and forwards
         the run config (so middleware sees ``thread_id``/``app_config``/...)
      6. ``await agent.ainvoke(...)`` to completion (no checkpointer — stateless)
      7. writes the final text into ``state["node_outputs"][node_name]``

    When ``is_terminal`` is set (no other node depends on this one), the
    result is also appended as a final ``AIMessage`` to ``messages`` so it
    streams to the frontend and persists to the thread for the next turn.
    """

    async def run_node(state: ChainState, config: RunnableConfig) -> dict:
        sub_config = get_subagent_config(node.subagent, app_config=app_config)
        if sub_config is None:
            raise ValueError(f"Chain node {node_name!r} references unknown subagent {node.subagent!r}. Define it under subagents.custom_agents or use a built-in.")
        if sub_config.workflow:
            raise ValueError(f"Chain node {node_name!r} references subagent {node.subagent!r} which is a workflow subagent — chain nodes must reference create_agent-based subagents.")

        # Resolve the parent model from the chain run config (the user's
        # selected model) so ``model: inherit`` subagents use the right model.
        configurable = (config.get("configurable") or {}) if config else {}
        context = (config.get("context") or {}) if config else {}
        parent_model = configurable.get("model_name") or context.get("model_name")

        effective_model = resolve_subagent_model_name(sub_config, parent_model, app_config=app_config)

        from deerflow.tools import get_available_tools

        available_tools = get_available_tools(
            model_name=effective_model,
            subagent_enabled=False,
            app_config=app_config,
        )
        filtered_tools = _filter_tools(available_tools, sub_config.tools, sub_config.disallowed_tools)
        agent = build_subagent_agent(
            sub_config,
            filtered_tools,
            app_config=app_config,
            model_name=effective_model,
        )

        node_outputs = dict(state.get("node_outputs") or {})
        user_input = _extract_user_input(state)
        prompt = _render_prompt(node, user_input, node_outputs)

        # Seed the subagent state with sandbox/thread_data so sandbox + file
        # tools operate in the same workspace as the chain.
        sub_state: dict[str, Any] = {"messages": [HumanMessage(content=prompt)]}
        if state.get("sandbox") is not None:
            sub_state["sandbox"] = state["sandbox"]
        if state.get("thread_data") is not None:
            sub_state["thread_data"] = state["thread_data"]

        # Forward the run config so middleware (SandboxMiddleware, ...) sees
        # the parent thread_id / app_config / user identity. Override the
        # recursion limit with the subagent's own max_turns and flag the run
        # as a subagent (mirrors SubagentExecutor's context assembly).
        sub_run_config = RunnableConfig(
            recursion_limit=sub_config.max_turns,
            configurable=dict(configurable),
            context={**(context or {}), "is_subagent": True},
            metadata=dict((config.get("metadata") or {}) if config else {}),
            tags=list((config.get("tags") or []) if config else []),
        )

        logger.info("Chain node %r running subagent %r", node_name, node.subagent)
        final_state = await agent.ainvoke(sub_state, config=sub_run_config)
        result_text = _extract_final_text(final_state)

        node_outputs[node_name] = result_text
        result: dict[str, Any] = {"node_outputs": node_outputs}
        if is_terminal:
            # Persist the chain's final answer into the thread so subsequent
            # lead-agent turns can reference it, and so it streams to the
            # frontend as a normal assistant message.
            result["messages"] = [AIMessage(content=result_text)]
        return result

    return run_node
