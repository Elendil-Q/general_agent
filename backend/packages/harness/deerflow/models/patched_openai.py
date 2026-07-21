"""Patched ChatOpenAI that preserves thought_signature and reasoning_content.

Two provider-specific fields are handled, both of which standard
``langchain_openai.ChatOpenAI`` silently drops when serializing request payloads
or parsing responses:

1. ``thought_signature`` (Gemini thinking via an OpenAI-compatible gateway).
   The API requires that the ``thought_signature`` field on tool-call objects is
   echoed back verbatim in every subsequent request.  Standard
   ``langchain_openai.ChatOpenAI`` only serialises the standard tool-call fields
   (``id``, ``type``, ``function``) into the outgoing payload, dropping the
   signature and causing an HTTP 400 ``INVALID_ARGUMENT`` error::

       Unable to submit request because function call `<tool>` in the N. content
       block is missing a `thought_signature`.

2. ``reasoning_content`` (DeepSeek-style thinking APIs - DeepSeek via Novita /
   OpenRouter / SiliconFlow, MiMo, and other OpenAI-compatible thinking
   endpoints).  These APIs return ``reasoning_content`` in thinking mode and
   require that value to be replayed on *every* historical assistant message in
   multi-turn requests.  Without capture, the reasoning is lost; without
   replay, the API rejects the next call with::

       The reasoning_content in the thinking mode must be passed back to the API.

This module fixes both by overriding:

* ``_get_request_payload`` - re-inject ``thought_signature`` onto tool-call
  objects and ``reasoning_content`` onto assistant messages in the outgoing
  payload.
* ``_convert_chunk_to_generation_chunk`` - capture ``reasoning_content`` from
  streaming deltas into ``AIMessageChunk.additional_kwargs``.
* ``_create_chat_result`` - capture ``reasoning_content`` from non-streaming
  choice messages into ``AIMessage.additional_kwargs``.

All overrides call ``super()`` first and only attach fields when present, so
non-reasoning / non-signed responses are byte-for-byte identical to base
``ChatOpenAI`` behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from deerflow.models.assistant_payload_replay import restore_assistant_payloads, restore_reasoning_content

_MISSING = object()


def _extract_reasoning_content(value: Any) -> str | object:
    """Return reasoning_content from a dict/Pydantic object, preserving empty strings."""
    if isinstance(value, Mapping):
        if "reasoning_content" in value and value["reasoning_content"] is not None:
            return value["reasoning_content"]
        return _MISSING

    reasoning = getattr(value, "reasoning_content", _MISSING)
    if reasoning is not _MISSING and reasoning is not None:
        return reasoning

    model_extra = getattr(value, "model_extra", None)
    if isinstance(model_extra, Mapping) and "reasoning_content" in model_extra and model_extra["reasoning_content"] is not None:
        return model_extra["reasoning_content"]

    return _MISSING


def _with_reasoning_content(message: AIMessage | AIMessageChunk, reasoning: str) -> AIMessage | AIMessageChunk:
    additional_kwargs = dict(message.additional_kwargs)
    if additional_kwargs.get("reasoning_content") != reasoning:
        additional_kwargs["reasoning_content"] = reasoning
    return message.model_copy(update={"additional_kwargs": additional_kwargs})


def _get_typed_choice_message(response: Any, index: int) -> Any:
    choices = getattr(response, "choices", None)
    if choices is None:
        return None
    try:
        return choices[index].message
    except (AttributeError, IndexError, TypeError):
        return None


class PatchedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with ``thought_signature`` and ``reasoning_content`` preservation.

    When using Gemini with thinking enabled via an OpenAI-compatible gateway,
    the API expects ``thought_signature`` to be present on tool-call objects in
    multi-turn conversations.  When using a DeepSeek-style thinking endpoint,
    the API expects ``reasoning_content`` to be present on every historical
    assistant message in multi-turn conversations.  This patched version
    captures ``reasoning_content`` on fresh responses and restores both fields
    into the serialised request payload before it is sent to the API.

    Usage in ``config.yaml``::

        - name: gemini-2.5-pro-thinking
          display_name: Gemini 2.5 Pro (Thinking)
          use: deerflow.models.patched_openai:PatchedChatOpenAI
          model: google/gemini-2.5-pro-preview
          api_key: $GEMINI_API_KEY
          base_url: https://<your-openai-compat-gateway>/v1
          max_tokens: 16384
          supports_thinking: true
          supports_vision: true
          when_thinking_enabled:
            extra_body:
              thinking:
                type: enabled

    Note: with the factory auto-swap (``deerflow.models.factory``), bare
    ``langchain_openai:ChatOpenAI`` configs that declare ``supports_thinking:
    true`` are upgraded to this class automatically when thinking is enabled, so
    explicit selection is only needed to opt out of the auto-swap path.
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with ``thought_signature`` and ``reasoning_content`` preserved.

        Overrides the parent method to re-inject ``thought_signature`` fields
        on tool-call objects that were stored in
        ``additional_kwargs["tool_calls"]`` by LangChain but dropped during
        serialisation, and to re-inject ``reasoning_content`` onto assistant
        messages that carried it in ``additional_kwargs``.
        """
        # Capture the original LangChain messages *before* conversion so we can
        # access fields that the serialiser might drop.
        original_messages = self._convert_input(input_).to_messages()

        # Obtain the base payload from the parent implementation.
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        restore_assistant_payloads(payload.get("messages", []), original_messages, _restore_tool_call_signatures)
        restore_assistant_payloads(payload.get("messages", []), original_messages, restore_reasoning_content)

        return payload

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return None

        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning = _extract_reasoning_content(delta)
            if reasoning is not _MISSING and isinstance(generation_chunk.message, AIMessageChunk):
                generation_chunk = ChatGenerationChunk(
                    message=_with_reasoning_content(generation_chunk.message, reasoning),
                    generation_info=generation_chunk.generation_info,
                )

        return generation_chunk

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices", [])

        patched_generations: list[ChatGeneration] | None = None
        for index, generation in enumerate(result.generations):
            choice = choices[index] if index < len(choices) else {}
            choice_message = choice.get("message", {}) if isinstance(choice, Mapping) else {}
            reasoning = _extract_reasoning_content(choice_message)
            if reasoning is _MISSING and not isinstance(response, dict):
                reasoning = _extract_reasoning_content(_get_typed_choice_message(response, index))

            message = generation.message
            if reasoning is not _MISSING and isinstance(message, AIMessage):
                if patched_generations is None:
                    patched_generations = list(result.generations)
                patched_generations[index] = ChatGeneration(
                    message=_with_reasoning_content(message, reasoning),
                    generation_info=generation.generation_info,
                )

        return ChatResult(generations=patched_generations or result.generations, llm_output=result.llm_output)


def _restore_tool_call_signatures(payload_msg: dict, orig_msg: AIMessage) -> None:
    """Re-inject ``thought_signature`` onto tool-call objects in *payload_msg*.

    When the Gemini OpenAI-compatible gateway returns a response with function
    calls, each tool-call object may carry a ``thought_signature``.  LangChain
    stores the raw tool-call dicts in ``additional_kwargs["tool_calls"]`` but
    only serialises the standard fields (``id``, ``type``, ``function``) into
    the outgoing payload, silently dropping the signature.

    This function matches raw tool-call entries (by ``id``, falling back to
    positional order) and copies the signature back onto the serialised
    payload entries.
    """
    raw_tool_calls: list[dict] = orig_msg.additional_kwargs.get("tool_calls") or []
    payload_tool_calls: list[dict] = payload_msg.get("tool_calls") or []

    if not raw_tool_calls or not payload_tool_calls:
        return

    # Build an id -> raw_tc lookup for efficient matching.
    raw_by_id: dict[str, dict] = {}
    for raw_tc in raw_tool_calls:
        tc_id = raw_tc.get("id")
        if tc_id:
            raw_by_id[tc_id] = raw_tc

    for idx, payload_tc in enumerate(payload_tool_calls):
        # Try matching by id first, then fall back to positional.
        raw_tc = raw_by_id.get(payload_tc.get("id", ""))
        if raw_tc is None and idx < len(raw_tool_calls):
            raw_tc = raw_tool_calls[idx]

        if raw_tc is None:
            continue

        # The gateway may use either snake_case or camelCase.
        sig = raw_tc.get("thought_signature") or raw_tc.get("thoughtSignature")
        if sig:
            payload_tc["thought_signature"] = sig
