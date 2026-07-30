"""Wiring tests: enabling ``ask_clarification`` (HITL) inside subagents.

Covers the Route 甲 prerequisites (see the plan):
- ``general-purpose`` allows ``ask_clarification``.
- ``SubagentExecutor`` stores ``clarification_interrupt_enabled`` for forwarding
  into the subagent runtime context (pinned in test_subagent_interrupt_and_resume.py).
- ``ClarificationMiddleware`` is attached last to the subagent chain (pinned in
  test_tool_error_handling_middleware.py).
"""

from __future__ import annotations

from deerflow.subagents.builtins.general_purpose import GENERAL_PURPOSE_CONFIG


def test_general_purpose_subagent_allows_ask_clarification():
    """general-purpose may now pause for human input via ask_clarification."""
    assert "ask_clarification" not in GENERAL_PURPOSE_CONFIG.disallowed_tools
    # task (nesting) and present_files stay disallowed.
    assert "task" in GENERAL_PURPOSE_CONFIG.disallowed_tools
    assert "present_files" in GENERAL_PURPOSE_CONFIG.disallowed_tools
