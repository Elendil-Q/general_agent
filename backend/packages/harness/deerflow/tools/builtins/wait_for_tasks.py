"""Wait-for-tasks tool: blocks until detached subagents complete."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langgraph.config import get_stream_writer

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 300  # fallback when no config available
_TIMEOUT_BUFFER_SECONDS = 60


@tool("wait_for_tasks", parse_docstring=True)
async def wait_for_tasks(
    task_ids: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime,
) -> str:
    """Wait for detached subagents to complete and collect their results.

    Blocks until each task_id reaches a stopped state (COMPLETED, FAILED,
    CANCELLED, TIMED_OUT, INTERRUPTED, or IDLE). Returns a JSON map keyed by task_id.

    Args:
        task_ids: List of task IDs to wait for (returned by task(detached=True)).
    """
    from deerflow.subagents.agent_registry import agent_registry
    from deerflow.subagents.event_bus import event_bus
    from deerflow.subagents.sse_bridge import sse_bridge

    thread_id = runtime.context.get("thread_id") if runtime.context else None
    writer = get_stream_writer()
    if thread_id:
        sse_bridge.register_writer(thread_id, writer)

    try:
        results: dict[str, dict] = {}
        pending = set(task_ids)
        poll_count = 0
        # Dynamic timeout: max(timeout_seconds of awaited) + buffer
        max_timeout = DEFAULT_TIMEOUT_SECONDS
        for tid in task_ids:
            ref = agent_registry.get(tid)
            if ref is not None:
                max_timeout = max(max_timeout, ref.config.timeout_seconds)
        effective_timeout = max_timeout + _TIMEOUT_BUFFER_SECONDS
        max_polls = max(effective_timeout // DEFAULT_POLL_SECONDS, 1)

        while pending and poll_count < max_polls:
            done: set[str] = set()
            for tid in list(pending):
                ref = agent_registry.get(tid)
                if ref is None:
                    results[tid] = {"status": "unknown", "error": "task not found in registry"}
                    done.add(tid)
                    continue

                status = ref.status
                if status.is_stopped:
                    result_val = ref.result.result if ref.result else None
                    error_val = ref.result.error if ref.result else None
                    results[tid] = {
                        "status": status.value,
                        "result": result_val,
                        "error": error_val,
                    }
                    done.add(tid)
                    event_bus.emit(
                        "subagent:lifecycle",
                        {
                            "event": status.value,
                            "task_id": tid,
                            "thread_id": thread_id or "",
                            "result": result_val,
                            "error": error_val,
                        },
                    )

            pending -= done
            if pending:
                await asyncio.sleep(DEFAULT_POLL_SECONDS)
                poll_count += 1

        # Report timed-out tasks
        for tid in pending:
            results[tid] = {"status": "pending", "error": "wait_for_tasks timed out"}

        return json.dumps(results, ensure_ascii=False, indent=2)
    finally:
        if thread_id:
            sse_bridge.unregister_writer(thread_id)
