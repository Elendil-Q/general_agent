"""Tests for subagent availability and prompt exposure."""

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.subagents import registry as registry_module


def test_get_available_subagent_names_returns_only_general_purpose(monkeypatch) -> None:
    """Only the general-purpose built-in subagent is registered now."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        registry_module,
        "get_or_new_subagent_storage",
        lambda *args, **kwargs: SimpleNamespace(list_names=lambda: [], load_subagent=lambda name: None),
    )

    names = registry_module.get_available_subagent_names()

    assert names == ["general-purpose"]


def test_build_subagent_section_uses_direct_execution_example(monkeypatch) -> None:
    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda **kw: ["general-purpose"])

    section = prompt_module._build_subagent_section(3)

    assert "**general-purpose**" in section
    assert "For ANY non-trivial task" in section


def test_general_purpose_subagent_prompt_mentions_workspace_relative_paths() -> None:
    from deerflow.subagents.builtins.general_purpose import GENERAL_PURPOSE_CONFIG

    assert "Treat `/mnt/user-data/workspace` as the default working directory for coding and file IO" in GENERAL_PURPOSE_CONFIG.system_prompt
    assert "`hello.txt`, `../uploads/input.csv`, and `../outputs/result.md`" in GENERAL_PURPOSE_CONFIG.system_prompt
