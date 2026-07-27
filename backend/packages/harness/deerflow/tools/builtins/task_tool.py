"""Task tool for delegating work to subagents."""

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langgraph.config import get_stream_writer

from deerflow.config import get_app_config
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.agent_registry import agent_registry
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.event_bus import event_bus
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
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


def _is_subagent_terminal(result: Any) -> bool:
    """Return whether a background subagent result is safe to clean up.

    INTERRUPTED is intentionally excluded: a paused subagent must stay resident
    so it can be resumed via ``resume_background_subagent``.
    """
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT}


def _summarize_interrupts(interrupts: Any) -> str:
    """Render the serialized ``__interrupt__`` payload as a short human-readable string.

    ``interrupts`` is the list of ``{value, id}`` dicts produced by
    ``serialize_lc_object`` on the raw ``Interrupt`` tuple. Falls back to a
    plain ``str`` rendering for anything unexpected so the lead agent always
    gets *some* question to surface.
    """
    if not interrupts:
        return "(no question provided)"
    parts: list[str] = []
    if isinstance(interrupts, (list, tuple)):
        items = interrupts
    else:
        items = [interrupts]
    for item in items:
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, str):
            parts.append(value)
        elif value is not None:
            try:
                import json

                parts.append(json.dumps(value, ensure_ascii=False, default=str))
            except Exception:
                parts.append(str(value))
    return " | ".join(parts) if parts else "(no question provided)"


async def _await_subagent_terminal(task_id: str, max_polls: int) -> Any | None:
    """Poll until the background subagent stops or we run out of polls.

    ``is_stopped`` (terminal OR INTERRUPTED) is the right predicate here: a
    paused subagent is no longer actively running, so a parent cancel should not
    block waiting on it. INTERRUPTED tasks are left resident for resume.
    """
    for _ in range(max_polls):
        result = get_background_task_result(task_id)
        if result is None:
            return None
        if getattr(result.status, "is_stopped", result.status.is_terminal):
            return result
        await asyncio.sleep(5)
    return None


async def _deferred_cleanup_subagent_task(task_id: str, trace_id: str, max_polls: int) -> None:
    """Keep polling a cancelled subagent until it can be safely removed."""
    cleanup_poll_count = 0
    while True:
        result = get_background_task_result(task_id)
        if result is None:
            return
        if _is_subagent_terminal(result):
            cleanup_background_task(task_id)
            return
        if cleanup_poll_count >= max_polls:
            logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
            return
        await asyncio.sleep(5)
        cleanup_poll_count += 1


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(task_id: str, trace_id: str, max_polls: int) -> None:
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(task_id, trace_id, max_polls))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id))


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


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """Summarize token usage records into a compact dict for SSE events."""
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


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
    detached: bool = False,
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
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

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
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config, user_id=runtime_user_id) if runtime_app_config is not None else get_available_subagent_names(user_id=runtime_user_id)

    # Get subagent configuration
    config = get_subagent_config(subagent_type, app_config=runtime_app_config, user_id=runtime_user_id) if runtime_app_config is not None else get_subagent_config(subagent_type, user_id=runtime_user_id)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

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

    # Subagents should not have subagent tools enabled (prevent recursive nesting)
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
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
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    if detached:
        task_id = executor.execute_async(prompt, task_id=tool_call_id)
        # execute_async already registers an AgentRef (PENDING) with the
        # correct SubagentResult. Bump to RUNNING; do NOT re-register or
        # we overwrite the SubagentResult reference with None.
        if agent_registry.update_status(task_id, SubagentStatus.RUNNING) is None:
            # Fallback: execute_async didn't register (e.g., mocked in tests).
            # Register a best-effort AgentRef without the SubagentResult.
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
                )
            )
        event_bus.emit(
            "subagent:lifecycle",
            {
                "event": "started",
                "task_id": task_id,
                "thread_id": thread_id,
                "description": description,
            },
        )
        return f"Task spawned. task_id={task_id}. Call wait_for_tasks([{task_id!r}]) to collect."

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    # Route 甲: when the subagent pauses on interrupt() we keep polling (the
    # lead run stays open, blocked in this tool) instead of returning a pause
    # string, so the lead model continues in the SAME turn once the subagent
    # resumes and completes. ``interrupt_announced`` ensures the
    # ``task_interrupted`` SSE event fires once per pause (not every 5s poll);
    # it resets when the subagent leaves INTERRUPTED so a re-interrupt re-announces.
    interrupt_announced = False
    # Polling timeout: execution timeout + 60s buffer, checked every 5s. Only
    # RUNNING polls advance the countdown (INTERRUPTED polls suspend it — see below).
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    sse_bridge.register_writer(thread_id, writer)
    # Send Task Started message
    event_bus.emit(
        "subagent:lifecycle",
        {
            "event": "started",
            "task_id": task_id,
            "thread_id": thread_id,
            "description": description,
        },
    )

    try:
        while True:
            result = get_background_task_result(task_id)

            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                event_bus.emit(
                    "subagent:lifecycle",
                    {
                        "event": "failed",
                        "task_id": task_id,
                        "thread_id": thread_id,
                        "error": "Task disappeared from background tasks",
                    },
                )
                cleanup_background_task(task_id)
                return f"Error: Task {task_id} disappeared from background tasks"

            # Log status changes for debugging
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                # Reset the interrupt-announce latch whenever the subagent
                # transitions OUT of INTERRUPTED (e.g. resumed -> RUNNING) so a
                # later re-interrupt fires task_interrupted again.
                if last_status is SubagentStatus.INTERRUPTED:
                    interrupt_announced = False
                last_status = result.status

            # Check for new AI messages and send task_running events
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    message = ai_messages[i]
                    event_bus.emit(
                        "subagent:progress",
                        {
                            "task_id": task_id,
                            "thread_id": thread_id,
                            "message": message,
                            "message_index": i + 1,  # 1-based index for display
                            "total_messages": current_message_count,
                        },
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # Check if task completed, failed, or timed out
            usage = _summarize_usage(getattr(result, "token_usage_records", None))
            if result.status == SubagentStatus.COMPLETED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                event_bus.emit(
                    "subagent:lifecycle",
                    {
                        "event": "completed",
                        "task_id": task_id,
                        "thread_id": thread_id,
                        "result": result.result,
                        "usage": usage,
                    },
                )
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                return f"Task Succeeded. Result: {result.result}"
            elif result.status == SubagentStatus.FAILED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                event_bus.emit(
                    "subagent:lifecycle",
                    {
                        "event": "failed",
                        "task_id": task_id,
                        "thread_id": thread_id,
                        "error": result.error,
                        "usage": usage,
                    },
                )
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                return f"Task failed. Error: {result.error}"
            elif result.status == SubagentStatus.CANCELLED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                event_bus.emit(
                    "subagent:lifecycle",
                    {
                        "event": "cancelled",
                        "task_id": task_id,
                        "thread_id": thread_id,
                        "error": result.error,
                        "usage": usage,
                    },
                )
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return "Task cancelled by user."
            elif result.status == SubagentStatus.TIMED_OUT:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                event_bus.emit(
                    "subagent:lifecycle",
                    {
                        "event": "timed_out",
                        "task_id": task_id,
                        "thread_id": thread_id,
                        "error": result.error,
                        "usage": usage,
                    },
                )
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return f"Task timed out. Error: {result.error}"
            elif result.status == SubagentStatus.INTERRUPTED:
                # Route 甲: the subagent paused on interrupt() awaiting human
                # input. Announce the interrupt ONCE (so the frontend shows the
                # form) but do NOT return — keep polling so the lead run stays
                # open (blocked in this tool) while the user answers. The
                # subagent is resumed via
                # POST /api/threads/{parent}/subagents/{task_id}/resume; when it
                # reaches COMPLETED the branch above returns the result and the
                # lead model continues in the SAME turn (no continuation-turn
                # mechanism). Token-usage reporting is deferred to COMPLETED (or
                # the cancel path) to avoid double-counting the paused turn.
                if not interrupt_announced:
                    event_bus.emit(
                        "subagent:lifecycle",
                        {
                            "event": "interrupted",
                            "task_id": task_id,
                            "thread_id": thread_id,
                            "subagent_thread_id": result.subagent_thread_id,
                            "description": description,
                            "interrupts": result.interrupts,
                        },
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} interrupted awaiting human input: {_summarize_interrupts(result.interrupts)}")
                    interrupt_announced = True
                # Fall through to the sleep + (suspended) timeout below; do NOT return.

            # Wait before the next poll (covers both still-RUNNING and paused-
            # INTERRUPTED states — the latter keeps the lead run open while the
            # user answers, re-checking every 5s whether the subagent was resumed).
            await asyncio.sleep(5)
            # Suspend the execution-timeout clock while INTERRUPTED: a slow
            # human reply must never trip the execution timeout, which exists to
            # catch a stuck *running* subagent. Only RUNNING polls advance it.
            if result.status != SubagentStatus.INTERRUPTED:
                poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                event_bus.emit(
                    "subagent:lifecycle",
                    {
                        "event": "timed_out",
                        "task_id": task_id,
                        "thread_id": thread_id,
                        "usage": usage,
                    },
                )
                # The task may still be running in the background. Signal cooperative
                # cancellation and schedule deferred cleanup to remove the entry from
                # _background_tasks once the background thread reaches a terminal state.
                request_cancel_background_task(task_id)
                _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
                return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"
    except asyncio.CancelledError:
        # Signal the background subagent thread to stop cooperatively.
        request_cancel_background_task(task_id)

        # Wait (shielded) for the subagent to reach a terminal state so the
        # final token usage snapshot is reported to the parent RunJournal
        # before the parent worker persists get_completion_data().
        terminal_result = None
        try:
            terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count))
        except asyncio.CancelledError:
            pass

        # Report whatever the subagent collected (even if we timed out).
        final_result = terminal_result or get_background_task_result(task_id)
        if final_result is not None:
            _report_subagent_usage(runtime, final_result)
        if final_result is not None and _is_subagent_terminal(final_result):
            cleanup_background_task(task_id)
        else:
            _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
    except Exception:
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
    finally:
        sse_bridge.unregister_writer(thread_id)
