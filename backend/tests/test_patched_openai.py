"""Tests for deerflow.models.patched_openai.PatchedChatOpenAI.

These tests verify that _restore_tool_call_signatures correctly re-injects
``thought_signature`` onto tool-call objects stored in
``additional_kwargs["tool_calls"]``, covering id-based matching, positional
fallback, camelCase keys, and several edge-cases.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from deerflow.models.patched_openai import PatchedChatOpenAI, _restore_tool_call_signatures

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RAW_TC_SIGNED = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "web_fetch", "arguments": '{"url":"http://example.com"}'},
    "thought_signature": "SIG_A==",
}

RAW_TC_UNSIGNED = {
    "id": "call_2",
    "type": "function",
    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
}

PAYLOAD_TC_1 = {
    "type": "function",
    "id": "call_1",
    "function": {"name": "web_fetch", "arguments": '{"url":"http://example.com"}'},
}

PAYLOAD_TC_2 = {
    "type": "function",
    "id": "call_2",
    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
}


def _ai_msg_with_raw_tool_calls(raw_tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", additional_kwargs={"tool_calls": raw_tool_calls})


def _make_model(**kwargs) -> PatchedChatOpenAI:
    return PatchedChatOpenAI(
        model="gpt-4o",
        api_key="test-key",
        base_url="https://example.com/v1",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Core: signed tool-call restoration
# ---------------------------------------------------------------------------


def test_tool_call_signature_restored_by_id():
    """thought_signature is copied to the payload tool-call matched by id."""
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_A=="


def test_tool_call_signature_for_parallel_calls():
    """For parallel function calls, only the first has a signature (per Gemini spec)."""
    payload_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [PAYLOAD_TC_1.copy(), PAYLOAD_TC_2.copy()],
    }
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED, RAW_TC_UNSIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_A=="
    assert "thought_signature" not in payload_msg["tool_calls"][1]


def test_tool_call_signature_camel_case():
    """thoughtSignature (camelCase) from some gateways is also handled."""
    raw_camel = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thoughtSignature": "SIG_CAMEL==",
    }
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = _ai_msg_with_raw_tool_calls([raw_camel])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_CAMEL=="


def test_tool_call_signature_positional_fallback():
    """When ids don't match, falls back to positional matching."""
    raw_no_id = {
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thought_signature": "SIG_POS==",
    }
    payload_tc = {
        "type": "function",
        "id": "call_99",
        "function": {"name": "web_fetch", "arguments": "{}"},
    }
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [payload_tc]}
    orig = _ai_msg_with_raw_tool_calls([raw_no_id])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_tc["thought_signature"] == "SIG_POS=="


# ---------------------------------------------------------------------------
# Edge cases: no-op scenarios for tool-call signatures
# ---------------------------------------------------------------------------


def test_tool_call_no_raw_tool_calls_is_noop():
    """No change when additional_kwargs has no tool_calls."""
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = AIMessage(content="", additional_kwargs={})

    _restore_tool_call_signatures(payload_msg, orig)

    assert "thought_signature" not in payload_msg["tool_calls"][0]


def test_tool_call_no_payload_tool_calls_is_noop():
    """No change when payload has no tool_calls."""
    payload_msg = {"role": "assistant", "content": "just text"}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert "tool_calls" not in payload_msg


def test_tool_call_unsigned_raw_entries_is_noop():
    """No signature added when raw tool-calls have no thought_signature."""
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_2.copy()]}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_UNSIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert "thought_signature" not in payload_msg["tool_calls"][0]


def test_tool_call_multiple_sequential_signatures():
    """Sequential tool calls each carry their own signature."""
    raw_tc_a = {
        "id": "call_a",
        "type": "function",
        "function": {"name": "check_flight", "arguments": "{}"},
        "thought_signature": "SIG_STEP1==",
    }
    raw_tc_b = {
        "id": "call_b",
        "type": "function",
        "function": {"name": "book_taxi", "arguments": "{}"},
        "thought_signature": "SIG_STEP2==",
    }
    payload_tc_a = {"type": "function", "id": "call_a", "function": {"name": "check_flight", "arguments": "{}"}}
    payload_tc_b = {"type": "function", "id": "call_b", "function": {"name": "book_taxi", "arguments": "{}"}}
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [payload_tc_a, payload_tc_b]}
    orig = _ai_msg_with_raw_tool_calls([raw_tc_a, raw_tc_b])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_tc_a["thought_signature"] == "SIG_STEP1=="
    assert payload_tc_b["thought_signature"] == "SIG_STEP2=="


# ---------------------------------------------------------------------------
# reasoning_content capture (streaming + non-streaming)
# ---------------------------------------------------------------------------


def test_captures_reasoning_content_from_streaming_delta():
    """reasoning_content in a streaming delta is captured into AIMessageChunk.additional_kwargs."""
    model = _make_model()

    first = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "I need "}}]},
        AIMessageChunk,
        {},
    )
    second = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"reasoning_content": "a tool."}}]},
        AIMessageChunk,
        {},
    )

    assert first is not None
    assert second is not None
    combined = first.message + second.message
    assert combined.additional_kwargs["reasoning_content"] == "I need a tool."


def test_captures_reasoning_content_from_non_streaming_choice():
    """reasoning_content on a non-streaming choice message is captured into AIMessage.additional_kwargs."""
    model = _make_model()
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The weather is sunny.",
                    "reasoning_content": "The tool returned sunny weather, so answer directly.",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }
        ],
        "model": "gpt-4o",
    }

    result = model._create_chat_result(response)
    message = result.generations[0].message

    assert message.content == "The weather is sunny."
    assert message.additional_kwargs["reasoning_content"] == "The tool returned sunny weather, so answer directly."


# ---------------------------------------------------------------------------
# reasoning_content replay (regression for the reported 400)
# ---------------------------------------------------------------------------


def test_replays_reasoning_content_on_historical_assistant_message():
    """Regression for the reported 400: after ask_clarification the next LLM
    call replays an AIMessage(reasoning_content + tool_call) -> ToolMessage
    sequence. The historical assistant payload must carry reasoning_content, or
    DeepSeek-style thinking APIs reject with:
    "The reasoning_content in the thinking mode must be passed back to the API."
    """
    model = _make_model()

    human = HumanMessage(content="Check Beijing weather.")
    ai = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "I need to call the weather tool."},
        tool_calls=[{"id": "call_weather", "name": "get_weather", "args": {"location": "Beijing"}}],
    )
    tool = ToolMessage(content="sunny", tool_call_id="call_weather")
    base_payload = {
        "messages": [
            {"role": "user", "content": "Check Beijing weather."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"location":"Beijing"}'},
                    }
                ],
            },
            {"role": "tool", "content": "sunny", "tool_call_id": "call_weather"},
        ]
    }

    with patch.object(type(model).__bases__[0], "_get_request_payload", return_value=base_payload):
        with patch.object(model, "_convert_input") as mock_convert:
            mock_convert.return_value = MagicMock(to_messages=lambda: [human, ai, tool])
            payload = model._get_request_payload([human, ai, tool])

    assert payload["messages"][1]["reasoning_content"] == "I need to call the weather tool."


def test_replays_thought_signature_and_reasoning_content_together():
    """Both restorers run on the same payload: thought_signature on tool-calls
    and reasoning_content on the assistant message. Regression that the
    reasoning_content addition did not drop thought_signature handling.
    """
    model = _make_model()

    human = HumanMessage(content="search")
    raw_tc = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thought_signature": "SIG_A==",
    }
    ai = AIMessage(content="", additional_kwargs={"tool_calls": [raw_tc], "reasoning_content": "plan"})
    payload_tc = {"type": "function", "id": "call_1", "function": {"name": "web_fetch", "arguments": "{}"}}
    base_payload = {
        "messages": [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": "", "tool_calls": [payload_tc]},
        ]
    }

    with patch.object(type(model).__bases__[0], "_get_request_payload", return_value=base_payload):
        with patch.object(model, "_convert_input") as mock_convert:
            mock_convert.return_value = MagicMock(to_messages=lambda: [human, ai])
            payload = model._get_request_payload([human, ai])

    assistant = payload["messages"][1]
    assert assistant["reasoning_content"] == "plan"
    assert assistant["tool_calls"][0]["thought_signature"] == "SIG_A=="
