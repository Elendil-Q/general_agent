"""Reminds a subagent to call ``yield`` when its turn ends without one.

Only attached when ``SubagentConfig.output`` is declared (the schema is
the signal that structured output is expected). Up to 3 reminders; the
3rd forces ``tool_choice`` to ``yield`` so the subagent must submit.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

_MAX_REMINDERS = 3
_REMINDER = "Your task is nearly complete. Call the `yield` tool to submit your structured result matching the declared output schema before finishing."


class YieldReminderMiddleware(AgentMiddleware[AgentState]):
    """Injects a yield reminder when an AIMessage lacks a yield tool_call."""

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def _remind(self, state: AgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None
        tool_calls = getattr(last, "tool_calls", None) or []
        if any(tc.get("name") == "yield" for tc in tool_calls):
            return None  # already yielding, don't disturb
        self._count += 1
        update: dict[str, Any] = {"messages": [SystemMessage(content=_REMINDER)]}
        if self._count >= _MAX_REMINDERS:
            update["tool_choice"] = {"type": "tool", "name": "yield"}
        return update

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._remind(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._remind(state)
