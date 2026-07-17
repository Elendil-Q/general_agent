"""Tests for SystemPromptMiddleware.

Verifies that the static system prompt is injected onto
``ModelRequest.system_message`` via the ``wrap_model_call`` path — which is
what makes it visible to Langfuse callback tracing (the prompt is no longer
held in a create_agent closure invisible to the model-call request pipeline).
"""

import asyncio
from unittest.mock import MagicMock

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from deerflow.agents.middlewares.system_prompt_middleware import SystemPromptMiddleware

# ---------------------------------------------------------------------------
# Helpers — mirror the ModelRequest stand-in pattern from
# tests/test_system_message_coalescing_middleware.py
# ---------------------------------------------------------------------------


def _make_request(system_message: SystemMessage | None, messages: list[BaseMessage]):
    request = MagicMock()
    request.system_message = system_message
    request.messages = list(messages)
    request.override = lambda **updates: _override_request(request, updates)
    return request


def _override_request(request, updates):
    new = MagicMock()
    new.system_message = updates.get("system_message", request.system_message)
    new.messages = updates.get("messages", request.messages)
    new.override = lambda **kw: _override_request(new, kw)
    return new


def _capture_handler():
    captured = []

    def handler(req):
        captured.append(req)
        return "response"

    return captured, handler


# ===========================================================================
# wrap_model_call
# ===========================================================================


class TestWrapModelCall:
    def test_injects_system_message_when_none(self):
        """Normal path (create_agent called with system_prompt=None): inject."""
        middleware = SystemPromptMiddleware("You are DeerFlow.")
        request = _make_request(system_message=None, messages=[HumanMessage(content="hi")])
        captured, handler = _capture_handler()

        result = middleware.wrap_model_call(request, handler)

        assert result == "response"
        assert len(captured) == 1
        sent = captured[0]
        assert isinstance(sent.system_message, SystemMessage)
        assert sent.system_message.content == "You are DeerFlow."
        # messages untouched
        assert sent.messages == [HumanMessage(content="hi")]

    def test_preserves_existing_system_message(self):
        """Idempotent: if system_message already set (e.g. coalescer merged
        block, or a retry re-entry), do not clobber it."""
        existing = SystemMessage(content="already here")
        middleware = SystemPromptMiddleware("should not be used")
        request = _make_request(system_message=existing, messages=[HumanMessage(content="hi")])
        captured, handler = _capture_handler()

        result = middleware.wrap_model_call(request, handler)

        assert result == "response"
        assert captured[0].system_message is existing

    def test_handler_receives_injected_request_not_original(self):
        """The original request must not be mutated; the override is what the
        inner handler sees, so the prompt travels through the middleware stack
        and is therefore traceable."""
        middleware = SystemPromptMiddleware("prompt text")
        request = _make_request(system_message=None, messages=[HumanMessage(content="hi")])
        captured, handler = _capture_handler()

        middleware.wrap_model_call(request, handler)

        assert request.system_message is None  # original unchanged
        assert captured[0].system_message is not None


# ===========================================================================
# awrap_model_call
# ===========================================================================


class TestAwrapModelCall:
    def test_injects_system_message_when_none(self):
        middleware = SystemPromptMiddleware("You are DeerFlow.")
        request = _make_request(system_message=None, messages=[HumanMessage(content="hi")])
        captured = []

        async def handler(req):
            captured.append(req)
            return "response"

        result = asyncio.run(middleware.awrap_model_call(request, handler))

        assert result == "response"
        assert isinstance(captured[0].system_message, SystemMessage)
        assert captured[0].system_message.content == "You are DeerFlow."

    def test_preserves_existing_system_message(self):
        existing = SystemMessage(content="already here")
        middleware = SystemPromptMiddleware("should not be used")
        request = _make_request(system_message=existing, messages=[HumanMessage(content="hi")])
        captured = []

        async def handler(req):
            captured.append(req)
            return "response"

        asyncio.run(middleware.awrap_model_call(request, handler))

        assert captured[0].system_message is existing


# ===========================================================================
# constructor / caching
# ===========================================================================


class TestConstructor:
    def test_caches_single_system_message_instance(self):
        """The same SystemMessage object is reused across turns so prefix-cache
        reuse semantics are preserved (identical content on every injection)."""
        middleware = SystemPromptMiddleware("cached prompt")
        first = middleware._system_message
        assert isinstance(first, SystemMessage)
        assert first.content == "cached prompt"
        # second access yields the same object
        assert middleware._system_message is first
