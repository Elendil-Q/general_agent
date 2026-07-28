"""Prevents the lead agent from terminating while detached subagent tasks are still running.

Gated on ``subagent_enabled`` (detached tasks only exist when subagents are on).
When the model produces a final AIMessage with no tool_calls but the AgentRegistry
has PENDING/RUNNING tasks for this thread, injects a reminder to call
``wait_for_tasks``. After 3 reminders, forces ``tool_choice`` to ``wait_for_tasks``.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

_MAX_REMINDERS = 3

_INFLIGHT_STATUSES = None  # lazily resolved to avoid import-time circular dependency


def _resolve_inflight_statuses():
    global _INFLIGHT_STATUSES
    if _INFLIGHT_STATUSES is None:
        from deerflow.subagents.executor import SubagentStatus

        _INFLIGHT_STATUSES = {SubagentStatus.PENDING, SubagentStatus.RUNNING}
    return _INFLIGHT_STATUSES


class PendingTaskGuardMiddleware(AgentMiddleware[AgentState]):
    """Injects a ``wait_for_tasks`` reminder when the agent tries to stop with in-flight detached tasks."""

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def _check(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls:
            return None

        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if not thread_id:
            return None

        from deerflow.subagents.agent_registry import agent_registry

        inflight = _resolve_inflight_statuses()
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

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._check(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._check(state, runtime)
