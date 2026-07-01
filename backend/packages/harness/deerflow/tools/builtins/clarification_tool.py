from typing import Literal

from langchain.tools import tool


# NOTE: ``return_direct`` MUST stay False (the default). When it is True,
# LangChain's ``create_agent`` tools->model conditional edge
# (``_make_tools_to_model_edge``) routes the turn to the ``after_agent`` exit
# whenever the model's tool call is ``ask_clarification``. That skips the model
# entirely, so on a structured clarification *resume* — where ``interrupt()``
# returns the user's answer and the middleware emits a ``ToolMessage`` with no
# ``goto`` — the agent never processes the answer and the run ends at
# ``SandboxMiddleware.after_agent`` with no assistant response. Keeping
# ``return_direct=False`` lets the resume loop back to the model so the answer
# is processed and a reply is produced.
@tool("ask_clarification", parse_docstring=True)
def ask_clarification_tool(
    question: str,
    interaction: Literal["single_choice", "multi_choice", "text", "free"],
    options: list[str] | None = None,
) -> str:
    """Ask the user for clarification when you need more information to proceed.

    The execution will be interrupted and the question will be presented to the
    user. Wait for the user's response before continuing.

    Choose the interaction mode that best fits the question:

    - **single_choice**: The user picks one option from ``options``. Use when the
      answer is one of a small known set. Always also lets the user type a custom
      answer ("other").
    - **multi_choice**: The user picks zero or more options from ``options``.
      Always also lets the user add a custom free-form supplement.
    - **text**: The user types free-form text. Use when no preset options apply.
    - **free**: Open-ended clarification that ends the current turn — the user
      replies in their next message. Use for complex, multi-part questions that
      a simple form cannot capture.

    Args:
        question: The clarification question to ask the user. Be specific and clear.
        interaction: How the user should answer — single_choice / multi_choice /
            text (structured form, run pauses and resumes with the answer) or free
            (open-ended, ends the turn).
        options: Preset choices for single_choice / multi_choice. Ignored for
            text / free. Always provide concrete options for the choice modes.
    """
    # Placeholder: the actual logic is handled by ClarificationMiddleware, which
    # intercepts this tool call and interrupts execution to present the question.
    return "Clarification request processed by middleware"
