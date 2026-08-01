"""Task tool for delegating work to subagents."""

import logging
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langgraph.config import get_stream_writer

from deerflow.config import get_app_config
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.agent_registry import agent_registry
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.event_bus import event_bus
from deerflow.subagents.executor import MAX_CONCURRENT_SUBAGENTS, SubagentStatus
from deerflow.subagents.sse_bridge import sse_bridge
from deerflow.tools.types import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# Cache subagent token usage by tool_call_id so TokenUsageMiddleware can
# write it back to the triggering AIMessage's usage_metadata.
_subagent_usage_cache: dict[str, dict[str, int]] = {}


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, enabled: bool = True) -> None:
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage


def pop_cached_subagent_usage(tool_call_id: str) -> dict | None:
    return _subagent_usage_cache.pop(tool_call_id, None)


def _find_usage_recorder(runtime: Any) -> Any | None:
    """Find a callback handler with ``record_external_llm_usage_records`` in the runtime config.

    LangChain may pass ``config["callbacks"]`` in three different shapes:

    - ``None`` (no callbacks registered): no recorder.
    - A plain ``list[BaseCallbackHandler]``: iterate it directly.
    - A ``BaseCallbackManager`` instance (e.g. ``AsyncCallbackManager`` on async
      tool runs): managers are not iterable, so we unwrap ``.handlers`` first.

    Any other shape (e.g. a single handler object accidentally passed without a
    list wrapper) cannot be iterated safely; treat it as "no recorder" rather
    than raise.
    """
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks:
        return None
    if not isinstance(callbacks, list):
        return None
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """Report subagent token usage to the parent RunJournal, if available.

    Each subagent task must be reported only once (guarded by usage_reported).
    """
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """Return the effective subagent skill allowlist under the parent policy."""
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
    """
    runtime_app_config = _get_runtime_app_config(runtime)
    runtime_user_id = resolve_runtime_user_id(runtime)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config, user_id=runtime_user_id) if runtime_app_config is not None else get_available_subagent_names(user_id=runtime_user_id)

    # Get subagent configuration
    config = get_subagent_config(subagent_type, app_config=runtime_app_config, user_id=runtime_user_id) if runtime_app_config is not None else get_subagent_config(subagent_type, user_id=runtime_user_id)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

    # Build config overrides
    overrides: dict = {}

    # Skills are loaded by SubagentExecutor per-session (aligned with Codex's pattern:
    # each subagent loads its own skills based on config, injected as conversation items).
    # No longer appended to system_prompt here.

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None
    user_id = None
    metadata: dict = {}

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # Get user_id for tracing (uses standard resolution order)
    user_id = resolve_runtime_user_id(runtime)

    # Propagate the authenticated runtime context so delegated tool calls are
    # evaluated by GuardrailMiddleware with the same identity/attribution as
    # the lead agent. Sourced from the server-side context written by
    # inject_authenticated_user_context (and run_id by the run worker); stays
    # None when absent (e.g. internal-auth runs) so guardrail behavior is
    # unchanged. Without this, role-aware policy silently mis-attributes any
    # tool call delegated to a subagent (user_role=None).
    parent_context = runtime.context if runtime is not None else None
    parent_context = parent_context if isinstance(parent_context, dict) else {}
    user_role = parent_context.get("user_role")
    oauth_provider = parent_context.get("oauth_provider")
    oauth_id = parent_context.get("oauth_id")
    run_id = parent_context.get("run_id")
    # Forward the structured-clarification gate so a subagent's
    # ClarificationMiddleware interrupts (web) instead of silently taking the
    # free goto=END path. Mirrors the lead agent's context flag.
    clarification_interrupt_enabled = parent_context.get("clarification_interrupt_enabled")

    # Nested-subagent lineage + permission gate. The spawning subagent's
    # executor marks the runtime context (``is_subagent``/``subagent_task_id``/
    # ``subagent_depth``/``nested_subagents_allowed``); the lead agent's context
    # has none of these, so lead spawns keep parent_task_id=None, depth=1.
    is_subagent = bool(parent_context.get("is_subagent"))
    nested_permitted = is_subagent and bool(parent_context.get("nested_subagents_allowed"))
    if is_subagent and not nested_permitted:
        return "Error: Nesting is not permitted for this subagent (the subagent type does not allow spawning subagents, or the maximum nesting depth has been reached)."
    parent_task_id = parent_context.get("subagent_task_id") if is_subagent else None
    depth = int(parent_context.get("subagent_depth") or 0) + 1
    if parent_task_id and agent_registry.count_active_children(parent_task_id) >= MAX_CONCURRENT_SUBAGENTS:
        return f"Error: Concurrent nested subagent limit reached — {MAX_CONCURRENT_SUBAGENTS} children of task {parent_task_id} are still active. Wait for one to finish before spawning more."

    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Inherit parent agent's tool_groups so subagents respect the same restrictions
    parent_tool_groups = metadata.get("tool_groups")
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

    # Subagents only get subagent tools when they are explicitly permitted to
    # nest (config opt-in + below MAX_SUBAGENT_DEPTH); the lead agent gets them
    # via its own subagent_enabled flag, not through this path.
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": nested_permitted,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    # Create executor
    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": parent_model,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "trace_id": trace_id,
        "user_id": user_id,
        "user_role": user_role,
        "oauth_provider": oauth_provider,
        "oauth_id": oauth_id,
        "run_id": run_id,
        "clarification_interrupt_enabled": clarification_interrupt_enabled,
        # tool_call_id becomes the task_id; passing it here lets the executor
        # derive a deterministic, API-addressable subagent_thread_id so an
        # interrupted subagent can be resumed by task_id.
        "task_id": tool_call_id,
        "parent_task_id": parent_task_id,
        "depth": depth,
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # Update to RUNNING; execute_async registers PENDING in agent_registry.
    if agent_registry.update_status(task_id, SubagentStatus.RUNNING) is None:
        from datetime import UTC, datetime

        from deerflow.subagents.agent_registry import AgentRef

        agent_registry.register(
            AgentRef(
                task_id=task_id,
                thread_id=thread_id or "",
                trace_id=trace_id or "",
                subagent_type=subagent_type,
                status=SubagentStatus.RUNNING,
                config=config,
                executor=executor,
                result=None,
                description=description,
                created_at=datetime.now(UTC),
                parent_task_id=parent_task_id,
                depth=depth,
            )
        )

    # Always register SSE writer so subagent events reach the frontend
    # from the moment it is spawned. A NESTED run must not register: its
    # writer would overwrite the lead run's writer in the bridge (keyed by
    # thread_id); nested events already route through the lead's writer.
    if not is_subagent:
        writer = get_stream_writer()
        sse_bridge.register_writer(thread_id, writer)

    event_bus.emit(
        "subagent:lifecycle",
        {
            "event": "started",
            "task_id": task_id,
            "thread_id": thread_id,
            "description": description,
            "parent_task_id": parent_task_id,
            "depth": depth,
        },
    )

    return f"Task spawned. task_id={task_id}. Call wait_for_tasks([{task_id!r}]) to collect."
