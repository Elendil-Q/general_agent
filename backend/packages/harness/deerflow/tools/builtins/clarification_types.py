"""Schema for structured clarification interrupts.

When ``ask_clarification`` is called with a structured ``interaction`` (single
choice / multi choice / text), the middleware calls ``interrupt()`` with a
``ClarificationInterruptRequest`` as the value. The frontend reads this from
the SDK's ``__interrupt__`` channel and renders the matching form. The user's
answer comes back via ``Command(resume=...)`` and becomes the return value of
``interrupt()``.

Kept deliberately small to minimise the LLM's tool-call burden: only
``question``, ``interaction``, ``options`` and ``tool_call_id`` are carried.
``single_choice`` / ``multi_choice`` always allow free-form "other" input on
the frontend, so no flag is part of the schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Interaction = Literal["single_choice", "multi_choice", "text", "free"]


class ClarificationInterruptRequest(BaseModel):
    """Payload surfaced to the frontend when a clarification interrupts the run.

    Attributes:
        question: The question text to present to the user.
        interaction: How the user should answer. ``single_choice`` / ``multi_choice``
            render option buttons/checkboxes (plus a free-form "other" input);
            ``text`` renders a free-form text input. ``free`` never reaches here —
            it takes the legacy ``goto=END`` path.
        options: Preset choices for ``single_choice`` / ``multi_choice``.
        tool_call_id: The originating tool call id, so the frontend can correlate
            the interrupt with the assistant tool-call step.
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["clarification_request"] = "clarification_request"
    question: str
    interaction: Interaction
    options: list[str] | None = None
    tool_call_id: str = ""
