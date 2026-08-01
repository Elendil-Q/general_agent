"""Follow-up tool for reviving IDLE subagents."""

import logging
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langgraph.config import get_stream_writer

from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)


@tool("follow_up", parse_docstring=True)
async def follow_up(
    task_id: str,
    prompt: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime: Runtime,
) -> str:
    """Send a follow-up prompt to an IDLE subagent and revive it in the background.

    Use this tool when you want to give additional instructions or ask a
    follow-up question to a subagent that has finished its initial task
    and is waiting for further input (IDLE state).

    This tool is fire-and-forget (like ``task``): it revives the subagent
    and returns immediately WITHOUT the result. Call
    ``wait_for_tasks([task_id])`` afterwards to wait for the subagent and
    collect its response. Do not assume the subagent has finished when this
    tool returns.

    Args:
        task_id: The task ID of the subagent to follow up with.
        prompt: The new prompt or instruction for the subagent.

    Returns:
        A confirmation that the follow-up started (the subagent keeps
        running in the background), or an error message if the subagent is
        not idle or has expired.
    """
    from deerflow.subagents.agent_registry import agent_registry
    from deerflow.subagents.executor import SubagentStatus
    from deerflow.subagents.lifecycle import lifecycle_manager
    from deerflow.subagents.sse_bridge import sse_bridge

    # Look up the agent in the registry
    ref = agent_registry.get(task_id)
    if ref is None or ref.status != SubagentStatus.IDLE:
        return f"Error: subagent {task_id} is not idle (status={ref.status.value if ref else 'unknown'}) or does not exist. Only IDLE subagents can receive follow-up prompts."

    executor = ref.executor
    if executor is None:
        return f"Error: subagent {task_id} has no live executor (it may have expired or been created in a previous process)."

    # Cancel the IDLE TTL — we're reviving this subagent
    lifecycle_manager.cancel_ttl(task_id)

    # Register SSE writer so progress events reach the parent run.
    # The ``revived`` lifecycle event is emitted inside
    # ``SubagentExecutor._acontinue`` (the canonical point, fires for
    # both the tool and direct callers). Do not re-emit here; the writer
    # registered below routes that event to this lead run's SSE stream.
    # No matching unregister: the revived subagent is still active, and
    # ``sse_bridge.unregister_writer`` keeps writers alive while any
    # non-terminal subagent remains; ``wait_for_tasks``/``task`` manage
    # the writer lifecycle for the collecting run.
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    writer = get_stream_writer()
    # A nested run (inside a subagent) must not overwrite the lead run's
    # writer in the bridge; nested events route through the lead's writer.
    if thread_id and not (runtime.context or {}).get("is_subagent"):
        sse_bridge.register_writer(thread_id, writer)

    # Fire-and-forget: revive the subagent in the background. The
    # continuation mutates the REGISTERED result holder, so a subsequent
    # interrupt is resumable via the resume endpoint, and wait_for_tasks
    # collects the outcome from the registry mirror.
    executor.continue_async(prompt, task_id)

    return f"Follow-up started for subagent {task_id}; it is running in the background. Call wait_for_tasks([{task_id!r}]) to wait for it and collect its response."
