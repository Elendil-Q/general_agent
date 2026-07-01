"""Tests for ClarificationMiddleware.

Covers:
- options normalization (JSON-string / scalar / list) for the free path
- structured path (single_choice / multi_choice / text) calls interrupt() and,
  on resume, returns a ToolMessage with the answer (no goto=END)
- free path and IM-gate-off fall back to goto=END
- interrupt payload shape (ClarificationInterruptRequest)
- idempotent stable message ids
- regression: after a clarification resume the agent must loop back to the
  model (it must not be routed to the ``after_agent`` exit by
  ``return_direct=True`` on the ``ask_clarification`` tool)
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from langgraph.types import Command

from deerflow.agents.middlewares import clarification_middleware as cm_module
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.tools.builtins.clarification_tool import ask_clarification_tool
from deerflow.tools.builtins.clarification_types import ClarificationInterruptRequest


@pytest.fixture
def middleware():
    return ClarificationMiddleware()


def _request(args: dict, *, tool_call_id: str = "call-1", runtime_enabled: bool | None = None):
    """Build a ToolCallRequest-like SimpleNamespace.

    ``runtime_enabled`` controls the clarification_interrupt_enabled gate:
    True/False sets it explicitly; None means no runtime at all.
    """
    ctx = {}
    if runtime_enabled is not None:
        ctx["clarification_interrupt_enabled"] = runtime_enabled
    runtime = SimpleNamespace(context=ctx) if runtime_enabled is not None else None
    return SimpleNamespace(
        tool_call={
            "name": "ask_clarification",
            "id": tool_call_id,
            "args": args,
        },
        runtime=runtime,
    )


class TestNormalizeOptions:
    """Options normalization shared by both paths."""

    def test_options_as_native_list(self, middleware):
        assert middleware._normalize_options(["dev", "staging", "prod"]) == ["dev", "staging", "prod"]

    def test_options_as_json_string(self, middleware):
        assert middleware._normalize_options(json.dumps(["dev", "staging", "prod"])) == ["dev", "staging", "prod"]

    def test_options_as_json_string_scalar(self, middleware):
        assert middleware._normalize_options(json.dumps("development")) == ["development"]

    def test_options_as_plain_string(self, middleware):
        assert middleware._normalize_options("just one option") == ["just one option"]

    def test_options_none(self, middleware):
        assert middleware._normalize_options(None) == []

    def test_options_empty_list(self, middleware):
        assert middleware._normalize_options([]) == []

    def test_options_missing(self, middleware):
        assert middleware._normalize_options(None) == []

    def test_options_json_string_with_mixed_types(self, middleware):
        assert middleware._normalize_options(json.dumps(["Option A", 2, True, None])) == ["Option A", "2", "True", "None"]


class TestFreePath:
    """free interaction (or IM gate off) -> markdown message + goto=END."""

    def test_free_path_goto_end_with_options(self, middleware):
        request = _request(
            {"question": "Which env?", "interaction": "free", "options": ["dev", "prod"]},
            runtime_enabled=True,
        )
        result = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))
        assert result.goto == "__end__"
        msg = result.update["messages"][0]
        assert "Which env?" in msg.content
        assert "1. dev" in msg.content
        assert "2. prod" in msg.content
        assert msg.name == "ask_clarification"

    def test_free_path_no_options(self, middleware):
        request = _request({"question": "Tell me more", "interaction": "free"}, runtime_enabled=True)
        result = middleware.wrap_tool_call(request, lambda _req: None)
        assert result.goto == "__end__"
        assert "1." not in result.update["messages"][0].content

    def test_im_gate_off_falls_back_to_goto_end(self, middleware):
        """Even with single_choice, no gate -> goto=END (IM channels)."""
        request = _request(
            {"question": "Which env?", "interaction": "single_choice", "options": ["dev", "prod"]},
            runtime_enabled=False,
        )
        result = middleware.wrap_tool_call(request, lambda _req: None)
        assert result.goto == "__end__"

    def test_no_runtime_falls_back_to_goto_end(self, middleware):
        request = _request(
            {"question": "Which env?", "interaction": "single_choice", "options": ["dev", "prod"]},
            runtime_enabled=None,
        )
        result = middleware.wrap_tool_call(request, lambda _req: None)
        assert result.goto == "__end__"


class TestStructuredPath:
    """single_choice/multi_choice/text with gate on -> interrupt() + resume returns ToolMessage."""

    def _run_with_interrupt(self, middleware, request, resume_answer):
        """Run the middleware; first call raises GraphInterrupt, second returns the answer."""
        call_count = {"n": 0}

        def fake_interrupt(payload):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Capture the payload for assertions, then raise.
                fake_interrupt.last_payload = payload
                from langgraph.errors import GraphInterrupt

                raise GraphInterrupt(payload)
            return resume_answer

        with patch.object(cm_module, "interrupt", side_effect=fake_interrupt):
            # First invocation: should raise GraphInterrupt.
            with pytest.raises(Exception) as exc_info:
                middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))
            assert "GraphInterrupt" in type(exc_info.value).__name__

            # Second invocation (resume): returns Command with ToolMessage, no goto=END.
            result = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))

        return result, fake_interrupt.last_payload

    def test_single_choice_interrupts_then_resumes_with_answer(self, middleware):
        request = _request(
            {"question": "Which env?", "interaction": "single_choice", "options": ["dev", "prod"]},
            runtime_enabled=True,
            tool_call_id="call-sc",
        )
        result, payload = self._run_with_interrupt(middleware, request, "staging")

        # No goto=END — agent continues the turn.
        assert getattr(result, "goto", None) != "__end__"
        msg = result.update["messages"][0]
        assert msg.content == "staging"
        assert msg.tool_call_id == "call-sc"
        assert msg.name == "ask_clarification"

    def test_interrupt_payload_shape(self, middleware):
        request = _request(
            {"question": "Which env?", "interaction": "single_choice", "options": ["dev", "prod"]},
            runtime_enabled=True,
            tool_call_id="call-payload",
        )
        _, payload = self._run_with_interrupt(middleware, request, "dev")

        # payload is the JSON-dumped ClarificationInterruptRequest.
        parsed = ClarificationInterruptRequest.model_validate(payload)
        assert parsed.type == "clarification_request"
        assert parsed.question == "Which env?"
        assert parsed.interaction == "single_choice"
        assert parsed.options == ["dev", "prod"]
        assert parsed.tool_call_id == "call-payload"

    def test_multi_choice_path_interrupts(self, middleware):
        request = _request(
            {"question": "Which langs?", "interaction": "multi_choice", "options": ["py", "ts"]},
            runtime_enabled=True,
        )
        result, _ = self._run_with_interrupt(middleware, request, '{"checked": ["py"], "extra": ""}')
        assert getattr(result, "goto", None) != "__end__"
        assert result.update["messages"][0].content == '{"checked": ["py"], "extra": ""}'

    def test_text_path_interrupts(self, middleware):
        request = _request(
            {"question": "Describe the target", "interaction": "text"},
            runtime_enabled=True,
        )
        result, _ = self._run_with_interrupt(middleware, request, "a free-form answer")
        assert getattr(result, "goto", None) != "__end__"
        assert result.update["messages"][0].content == "a free-form answer"

    def test_options_normalized_in_payload(self, middleware):
        """JSON-string options are normalized before going into the interrupt payload."""
        request = _request(
            {"question": "Which?", "interaction": "single_choice", "options": json.dumps(["a", "b"])},
            runtime_enabled=True,
        )
        _, payload = self._run_with_interrupt(middleware, request, "a")
        parsed = ClarificationInterruptRequest.model_validate(payload)
        assert parsed.options == ["a", "b"]


class TestIdempotency:
    """Free-path tool-call retries should not duplicate messages in state."""

    def test_repeated_free_call_uses_stable_message_id(self, middleware):
        request = _request(
            {"question": "Which environment?", "interaction": "free", "options": ["dev", "prod"]},
            runtime_enabled=True,
            tool_call_id="call-clarify-1",
        )
        first = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))
        second = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))

        first_message = first.update["messages"][0]
        second_message = second.update["messages"][0]
        assert first_message.id == "clarification:call-clarify-1"
        assert second_message.id == first_message.id

        merged = add_messages(add_messages([], [first_message]), [second_message])
        assert len(merged) == 1
        assert merged[0].id == "clarification:call-clarify-1"

    def test_missing_tool_call_id_still_gets_stable_message_id(self, middleware):
        request = _request(
            {"question": "Which environment?", "interaction": "free"},
            runtime_enabled=True,
        )
        # No id in the tool_call -> SimpleNamespace built with default call-1; clear it.
        request.tool_call = {"name": "ask_clarification", "args": {"question": "Which?", "interaction": "free"}}

        first = middleware.wrap_tool_call(request, lambda _req: None)
        second = middleware.wrap_tool_call(request, lambda _req: None)
        assert first.update["messages"][0].id.startswith("clarification:")
        assert second.update["messages"][0].id == first.update["messages"][0].id


# ---------------------------------------------------------------------------
# Regression: clarification resume must loop back to the model
# ---------------------------------------------------------------------------
#
# Bug: ``ask_clarification`` was decorated with ``return_direct=True``.
# LangChain's ``create_agent`` wires the tools->model conditional edge
# (``_make_tools_to_model_edge``) to route the turn to the ``after_agent``
# exit node whenever every client-side tool call in the last AIMessage has
# ``return_direct=True``. On a clarification *resume*, ``interrupt()`` returns
# the user's answer and the middleware returns ``Command(update={ToolMessage})``
# with no ``goto``; because the tool is ``return_direct`` the edge routes to
# ``after_agent`` and the model is never called — the run ends at
# ``SandboxMiddleware.after_agent`` (the last hook in the exit chain) with no
# assistant response. The tests below pin the fix: the agent must call the model
# after the clarification so it can process the answer.


class _ClarificationAgentModel(BaseChatModel):
    """Fake chat model for the create_agent routing regression tests.

    Turn 1 emits an ``ask_clarification`` tool call; every later turn emits a
    plain text answer with no tool calls so the agent loop terminates.
    """

    interaction: str = "single_choice"
    options: list[str] | None = None
    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-clarification-agent"

    def bind_tools(self, tools, **kwargs):
        # create_agent binds tools onto the model; responses are hard-coded so
        # there is nothing to actually bind, but the method must not raise.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-clarify-1",
                        "name": "ask_clarification",
                        "args": {
                            "question": "Which environment?",
                            "interaction": self.interaction,
                            "options": self.options,
                        },
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="Got it — proceeding with the chosen option.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class _AlwaysInterruptClarification(ClarificationMiddleware):
    """Test double that always takes the structured interrupt path.

    The real middleware gates on ``runtime.context["clarification_interrupt_enabled"]``,
    which is populated by the Gateway's LangGraph runtime from ``config["context"]``.
    A bare ``create_agent().invoke()`` does not surface that plumbing, so this
    subclass forces the structured path to isolate the interrupt/resume *routing*
    behaviour (the thing the regression test cares about) from the context wiring.
    """

    def _interrupt_enabled(self, request) -> bool:  # type: ignore[override]
        return True


def test_ask_clarification_tool_is_not_return_direct() -> None:
    """Guard: ask_clarification must not be return_direct=True.

    With return_direct=True the tools->model edge routes the resume turn to the
    after_agent exit, skipping the model — the agent ends with no response.
    See the module-level comment above for the full failure mode.
    """
    assert ask_clarification_tool.return_direct is False


def test_ask_clarification_free_path_loops_to_model() -> None:
    """After ask_clarification(free) the model must be called again.

    The free path returns a ToolMessage + goto=END, but create_agent's tools->model
    conditional edge ignores tool-returned ``goto`` and routes based on
    ``return_direct``. With the bug (return_direct=True) the turn exits to
    after_agent and the model is never called again (call_count stays at 1).
    With the fix the model loops back to voice the question and stop.
    """
    model = _ClarificationAgentModel(interaction="free", options=["dev", "prod"])
    agent = create_agent(
        model=model,
        tools=[ask_clarification_tool],
        middleware=[ClarificationMiddleware()],
    )

    result = agent.invoke({"messages": [HumanMessage(content="deploy the app")]})

    assert model.call_count == 2, "model was not called after ask_clarification — the tools->model edge routed to the after_agent exit (return_direct=True), so the agent would end with no response."
    final_ai = next(m for m in reversed(result["messages"]) if isinstance(m, AIMessage))
    assert final_ai.tool_calls == []
    assert final_ai.content


def test_ask_clarification_resume_reaches_model() -> None:
    """The reported bug: after a structured clarification interrupt + resume,
    the model must be called to process the answer.

    Previously the resume turn routed to the after_agent exit (LoopDetection ->
    Memory -> Sandbox) and the model was never called, so the run ended at
    SandboxMiddleware.after_agent with no assistant response. With the fix the
    model loops back and produces a response using the answer.
    """
    model = _ClarificationAgentModel(interaction="single_choice", options=["dev", "prod"])
    agent = create_agent(
        model=model,
        tools=[ask_clarification_tool],
        middleware=[_AlwaysInterruptClarification()],
        checkpointer=InMemorySaver(),
    )
    thread_id = "clarify-resume-1"
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    # Turn 1: model calls ask_clarification(single_choice) -> interrupt pauses.
    agent.invoke({"messages": [HumanMessage(content="deploy the app")]}, config=config)
    # The model emitted the tool call once, then the run paused on interrupt.
    assert model.call_count == 1, "model should have emitted the ask_clarification call once"

    # Turn 2: resume with the user's answer.
    result = agent.invoke(Command(resume="staging"), config=config)

    # The model MUST have been called a second time to process the answer.
    assert model.call_count == 2, "model was not called after resume — return_direct routed the resume turn to the after_agent exit, so the agent ended with no response."
    final_ai = next(m for m in reversed(result["messages"]) if isinstance(m, AIMessage))
    assert final_ai.tool_calls == []
    assert final_ai.content
