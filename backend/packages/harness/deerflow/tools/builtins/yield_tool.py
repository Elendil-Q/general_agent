# backend/packages/harness/deerflow/tools/builtins/yield_tool.py
"""The ``yield`` tool - a subagent submits its structured result.

Present in every subagent's tool set (added by the executor). Backward
compatible: subagents that never call it fall back to last-AIMessage
extraction. The collector is injected via a ContextVar set by the
executor before ``astream``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from deerflow.subagents.yield_protocol import _yield_collector_ctx


@tool("yield")
def yield_tool(
    data: Any,
    type: str | list[str] | None = None,
) -> str:
    """Submit a result. Call with no ``type`` (or a string) to finalize;
    call with a list of section names to accumulate incremental sections.
    """
    collector = _yield_collector_ctx.get()
    if collector is None:
        return "Error: no yield collector is active for this subagent."
    collector.record(data, type)
    if type is None or isinstance(type, str):
        return "Result submitted. You may stop."
    return f"Section recorded ({type}). Continue or submit final."
