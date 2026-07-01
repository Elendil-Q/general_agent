"""Middleware for intercepting clarification requests and presenting them to the user.

Two paths:

- **Structured (simple)**: ``interaction`` is ``single_choice`` / ``multi_choice``
  / ``text`` AND the runtime context flag ``clarification_interrupt_enabled`` is
  set (web frontend only). The middleware calls ``interrupt()`` with a
  ``ClarificationInterruptRequest`` payload; the frontend renders a form modal
  and resumes via ``Command(resume=...)``. ``interrupt()`` returns the user's
  answer on resume, which is wrapped in a ``ToolMessage`` and the agent continues
  the turn inline (no ``goto=END``).

- **Free (complex / IM)**: ``interaction == "free"`` or the interrupt flag is
  absent (e.g. IM channels, which cannot resume). The middleware renders the
  question as markdown in a ``ToolMessage`` and returns ``goto=END``. Note:
  ``create_agent``'s tools->model conditional edge ignores a ``goto`` returned
  from a tool and routes on ``return_direct`` instead, so with
  ``return_direct=False`` (required for the structured resume above) the agent
  loops back to the model, voices the clarification question, and stops — which
  reliably surfaces the question to IM users (who only see assistant text). The
  ``goto=END`` is retained as intent-documenting and would end the turn directly
  if LangGraph ever honours tool-returned ``goto`` from the tools node.
"""

import json
import logging
from collections.abc import Callable
from hashlib import sha256
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command, interrupt

from deerflow.tools.builtins.clarification_types import ClarificationInterruptRequest

logger = logging.getLogger(__name__)

# interaction values that take the structured interrupt path when the runtime
# gate is open. ``free`` always takes the legacy goto=END path.
_STRUCTURED_INTERACTIONS = {"single_choice", "multi_choice", "text"}


class ClarificationMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    pass


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    """Intercepts ``ask_clarification`` tool calls and surfaces them to the user.

    Structured interactions interrupt the run so a frontend form can collect the
    answer and resume; free/IM interactions fall back to a markdown message that
    ends the turn.
    """

    state_schema = ClarificationMiddlewareState

    def _stable_message_id(self, tool_call_id: str, suffix: str) -> str:
        """Build a deterministic message ID so retried calls replace, not append."""
        if tool_call_id:
            return f"clarification:{tool_call_id}"
        digest = sha256(suffix.encode("utf-8")).hexdigest()[:16]
        return f"clarification:{digest}"

    def _interrupt_enabled(self, request: ToolCallRequest) -> bool:
        """Whether the structured interrupt path is available for this run.

        Reads ``clarification_interrupt_enabled`` from the runtime context. The
        web frontend sets it; IM channels do not (they cannot resume an
        interrupted run), so they fall back to the legacy markdown + goto=END.
        """
        runtime = getattr(request, "runtime", None)
        if runtime is None:
            return False
        ctx = getattr(runtime, "context", None)
        if not isinstance(ctx, dict):
            return False
        return ctx.get("clarification_interrupt_enabled") is True

    def _normalize_options(self, options: object) -> list[str]:
        """Normalize ``options`` to a list[str].

        Some models serialize array parameters as JSON strings; deserialize and
        fall back gracefully so rendering never breaks.
        """
        if options is None:
            return []
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except (json.JSONDecodeError, TypeError):
                return [options]
        if isinstance(options, list):
            return [str(o) for o in options]
        return [str(options)]

    def _format_free_message(self, question: str, options: list[str]) -> str:
        """Render the free-form clarification as a markdown message."""
        parts = [f"❓ {question}"]
        if options:
            parts.append("")
            for i, option in enumerate(options, 1):
                parts.append(f"  {i}. {option}")
        return "\n".join(parts)

    def _handle_clarification(self, request: ToolCallRequest) -> Command:
        """Handle a clarification tool call.

        Raises ``GraphInterrupt`` (via ``interrupt()``) on the structured path
        the first time it runs; on resume ``interrupt()`` returns the user's
        answer and a ``ToolMessage`` is produced so the agent continues.
        """
        args = request.tool_call.get("args", {}) or {}
        question = str(args.get("question", ""))
        interaction = args.get("interaction", "free")
        options = self._normalize_options(args.get("options"))
        tool_call_id = str(request.tool_call.get("id", ""))

        if interaction in _STRUCTURED_INTERACTIONS and self._interrupt_enabled(request):
            return self._handle_structured(question, interaction, options, tool_call_id)

        return self._handle_free(question, options, tool_call_id)

    def _handle_structured(self, question: str, interaction: str, options: list[str], tool_call_id: str) -> Command:
        """Interrupt with a structured payload; on resume return the answer as a ToolMessage."""
        payload = ClarificationInterruptRequest(
            question=question,
            interaction=interaction,  # type: ignore[arg-type]
            options=options or None,
            tool_call_id=tool_call_id,
        )
        # First invocation raises GraphInterrupt (caught/reraised by the outer
        # ToolErrorHandlingMiddleware); on resume the graph re-executes this
        # tool call and interrupt() returns the user's answer. No manual cache:
        # LangGraph matches resume values to interrupts by order within the task.
        answer = interrupt(payload.model_dump(mode="json"))

        # The agent continues the turn with the user's answer as the tool result.
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        id=self._stable_message_id(tool_call_id, str(answer)),
                        content=str(answer),
                        tool_call_id=tool_call_id,
                        name="ask_clarification",
                    )
                ]
            },
        )

    def _handle_free(self, question: str, options: list[str], tool_call_id: str) -> Command:
        """Legacy path: markdown message + goto=END (turn ends)."""
        formatted_message = self._format_free_message(question, options)
        tool_message = ToolMessage(
            id=self._stable_message_id(tool_call_id, formatted_message),
            content=formatted_message,
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )
        return Command(
            update={"messages": [tool_message]},
            goto=END,
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """Intercept ask_clarification tool calls (sync)."""
        if request.tool_call.get("name") != "ask_clarification":
            return handler(request)
        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """Intercept ask_clarification tool calls (async)."""
        if request.tool_call.get("name") != "ask_clarification":
            return await handler(request)
        return self._handle_clarification(request)
