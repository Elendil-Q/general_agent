"""Middleware to inject the static system prompt via the model-call request path.

create_agent holds a static ``system_prompt`` argument in a closure variable
(``system_message``) inside ``model_node`` and only flattens it into the message
list at the very last moment (``_execute_model_sync``: ``[request.system_message, *messages]``).
Because that injection happens after the request leaves the traceable
middleware pipeline, observability backends (Langfuse) that instrument model
invocations via callbacks never see the system prompt as part of the request
payload — they only see ``request.messages``, which excludes the system block.

This middleware moves the injection one step earlier: instead of passing the
prompt to ``create_agent(system_prompt=...)`` (where it stays in a closure),
we pass ``system_prompt=None`` and register this middleware as the outermost
``wrap_model_call`` layer. It sets ``request.system_message`` from a cached
``SystemMessage`` instance so the prompt becomes an explicit field on the
``ModelRequest`` that flows through every middleware and the tracing handler.

Idempotent: only injects when ``request.system_message`` is already ``None``
(the normal path, since ``create_agent`` was called without ``system_prompt``).
If any inner middleware or a retry path already populated the field, the
existing value is preserved to avoid clobbering a coalesced or mutated prompt.

Placement: register BEFORE ``SystemMessageCoalescingMiddleware`` in
``build_middlewares`` (i.e. earlier in the list = outermost ``wrap_model_call``
layer, runs first). That way coalescer sees the merged set:
``request.system_message`` (this prompt) + the DynamicContext reminder
SystemMessages living inside ``request.messages``, and collapses them into one
leading SystemMessage — exactly the pre-refactor runtime behavior.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


class SystemPromptMiddleware(AgentMiddleware[AgentState]):
    """Inject a static system prompt onto ``ModelRequest.system_message``.

    The prompt string is converted to a ``SystemMessage`` once at construction
    time and reused across calls, preserving prefix-cache reuse semantics (the
    same prompt content/instance is prepended on every turn).
    """

    def __init__(self, system_prompt: str) -> None:
        self._system_message: SystemMessage = SystemMessage(content=system_prompt)

    def _maybe_inject(self, request: ModelRequest) -> ModelRequest:
        # ``request.system_message`` is None when create_agent was called
        # without ``system_prompt``. We only fill it in that case to remain
        # idempotent across retries and to not clobber inner-middleware
        # mutations (e.g. SystemMessageCoalescingMiddleware's merged block).
        if request.system_message is not None:
            return request
        return request.override(system_message=self._system_message)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._maybe_inject(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._maybe_inject(request))
