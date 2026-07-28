# backend/tests/test_wait_for_tasks_binding.py
"""Verify wait_for_tasks is included in SUBAGENT_TOOLS and properly importable."""

from __future__ import annotations


def test_wait_for_tasks_in_subagent_tools():
    from deerflow.tools.tools import SUBAGENT_TOOLS

    names = [t.name for t in SUBAGENT_TOOLS]
    assert "wait_for_tasks" in names


def test_wait_for_tasks_importable_from_builtins():
    from deerflow.tools.builtins import wait_for_tasks

    assert wait_for_tasks.name == "wait_for_tasks"


def test_subagent_tools_contains_all_three():
    from deerflow.tools.tools import SUBAGENT_TOOLS

    names = {t.name for t in SUBAGENT_TOOLS}
    assert names == {"task", "follow_up", "wait_for_tasks"}
