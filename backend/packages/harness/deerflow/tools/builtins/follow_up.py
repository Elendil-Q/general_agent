"""Follow-up tool for reviving IDLE subagents."""

import asyncio
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
    """Continue a completed (IDLE) subagent with a follow-up prompt.

    Use this tool when you want to give additional instructions or ask a
    follow-up question to a subagent that has finished its initial task
    and is waiting for further input (IDLE state).

    Args:
        task_id: The task ID of the subagent to follow up with.
        prompt: The new prompt or instruction for the subagent.

    Returns:
        A summary of the follow-up result: the subagent's response, or
        an error message if the subagent is not idle or has expired.
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
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    writer = get_stream_writer()
    if thread_id:
        sse_bridge.register_writer(thread_id, writer)

    try:
        # Block on the continuation — sync call, dispatched via the executor's
        # internal event-loop logic (mirrors executor.execute()).
        result = await asyncio.to_thread(executor.continue_with_prompt, prompt, task_id)

        if result.status == SubagentStatus.COMPLETED:
            return f"Follow-up completed. Result: {result.result}"
        elif result.status == SubagentStatus.FAILED:
            return f"Follow-up failed: {result.error}"
        elif result.status == SubagentStatus.CANCELLED:
            return f"Follow-up was cancelled: {result.error}"
        elif result.status == SubagentStatus.TIMED_OUT:
            return f"Follow-up timed out: {result.error}"
        elif result.status == SubagentStatus.IDLE:
            # keep_alive=True: subagent is idle again
            lifecycle_manager.adopt(task_id)
            return f"Follow-up completed. Subagent {task_id} is idle again, awaiting further follow-up."
        elif result.status == SubagentStatus.INTERRUPTED:
            return f"Follow-up paused: the subagent {task_id} is awaiting human input. Use the resume mechanism to continue."
        else:
            return f"Follow-up ended with status: {result.status.value}"
    finally:
        if thread_id:
            sse_bridge.unregister_writer(thread_id)
