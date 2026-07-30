"""Consolidated subagent-awareness middleware for the lead agent.

Injects real-time subagent status into the lead model's context (via
``wrap_model_call``) and enforces concurrent-subagent limits + pending-task
guard (via ``after_model``). Absorbs SubagentLimitMiddleware and
PendingTaskGuardMiddleware.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
from deerflow.subagents.executor import MAX_CONCURRENT_SUBAGENTS, SubagentStatus

_MAX_REMINDERS = 3
MIN_SUBAGENT_LIMIT = 2
MAX_SUBAGENT_LIMIT = 4


def _clamp_subagent_limit(value: int) -> int:
    return max(MIN_SUBAGENT_LIMIT, min(MAX_SUBAGENT_LIMIT, value))


class SubagentStatusMessage(SystemMessage):
    """Lead agent context: real-time subagent status snapshot."""

    @classmethod
    def from_refs(cls, refs: list) -> SubagentStatusMessage:

        running = [r for r in refs if r.status == SubagentStatus.RUNNING]
        interrupted = [r for r in refs if r.status == SubagentStatus.INTERRUPTED]
        completed = [r for r in refs if r.status == SubagentStatus.COMPLETED]
        idle = [r for r in refs if r.status == SubagentStatus.IDLE]
        failed = [r for r in refs if r.status == SubagentStatus.FAILED]

        sections: list[str] = ["<subagent-status>"]

        if running:
            sections.append(f"## Running ({len(running)})")
            for r in running:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): {r.description}")

        if completed:
            sections.append(f"## Completed ({len(completed)})")
            for r in completed:
                sections.append(f'- task {r.task_id} ({r.subagent_type}): Call wait_for_tasks(["{r.task_id}"]) to retrieve result.')

        if interrupted:
            sections.append(f"## Interrupted ({len(interrupted)})")
            for r in interrupted:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): Awaiting user input via POST /resume.")

        if idle:
            sections.append(f"## Idle ({len(idle)})")
            for r in idle:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): Parked. Use follow_up to continue.")

        if failed:
            sections.append(f"## Failed ({len(failed)})")
            for r in failed:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): {r.result.error if r.result else 'unknown error'}")

        sections.append("</subagent-status>")
        content = "\n".join(sections)

        return cls(
            content=content,
            additional_kwargs={
                "hide_from_ui": True,
                "subagent_status": True,
                "subagents": [{"task_id": r.task_id, "status": r.status.value, "subagent_type": r.subagent_type} for r in refs],
            },
        )


class SubagentContextMiddleware(AgentMiddleware[AgentState]):
    """Injects subagent status into lead context and enforces limits + guard.

    Args:
        max_concurrent: Maximum concurrent subagent calls (clamped [2,4]).
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SUBAGENTS) -> None:
        super().__init__()
        self.max_concurrent = _clamp_subagent_limit(max_concurrent)
        self._count = 0

    @override
    async def awrap_model_call(self, request: ModelRequest, handler) -> Any:
        thread_id = request.runtime.context.get("thread_id") if request.runtime.context else None
        if not thread_id:
            return await handler(request)

        from deerflow.subagents.agent_registry import agent_registry

        refs = agent_registry.list_by_thread(thread_id)
        if not refs:
            return await handler(request)

        status_msg = SubagentStatusMessage.from_refs(refs)
        augmented = request.override(messages=[*request.messages, status_msg])
        return await handler(augmented)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        # 1. Limit: truncate excess task tool calls (when AIMessage HAS task calls)
        update = self._enforce_limit(last)
        if update is not None:
            return update

        # 2. Guard: prevent premature termination (when AIMessage has NO tool calls)
        return self._enforce_guard(state, runtime, last)

    def _enforce_limit(self, last: AIMessage) -> dict[str, Any] | None:
        tool_calls = getattr(last, "tool_calls", None)
        if not tool_calls:
            return None

        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        if len(task_indices) <= self.max_concurrent:
            return None

        indices_to_drop = set(task_indices[self.max_concurrent :])
        truncated = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]
        updated_msg = clone_ai_message_with_tool_calls(last, truncated)
        return {"messages": [updated_msg]}

    def _enforce_guard(self, state: AgentState, runtime: Runtime, last: AIMessage) -> dict[str, Any] | None:
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls:
            return None

        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if not thread_id:
            return None

        from deerflow.subagents.agent_registry import agent_registry

        inflight = {SubagentStatus.PENDING, SubagentStatus.RUNNING}
        pending = [r for r in agent_registry.list_by_thread(thread_id) if r.status in inflight]
        if not pending:
            return None

        self._count += 1
        task_ids = [r.task_id for r in pending]
        reminder = f"You have {len(pending)} background subagent task(s) still running (task_ids: {task_ids}). You MUST call wait_for_tasks({task_ids}) to collect their results before finishing your response."
        update: dict[str, Any] = {"messages": [SystemMessage(content=reminder)]}
        if self._count >= _MAX_REMINDERS:
            update["tool_choice"] = {"type": "tool", "name": "wait_for_tasks"}
        return update
