"""Tests for the per-user shadow layer in the subagent registry.

The registry resolves subagent configs in this order:
1. Built-in subagents (names reserved - never shadowed by user files)
2. Per-user subagent files (Task 2 storage) - shadows the global custom layer
3. Global custom subagents (config.yaml ``custom_agents`` + shared YAML files)
4. config.yaml per-agent attribute overrides applied on top

Per-user files only consulted when ``user_id`` is provided; ``user_id=None``
paths are byte-identical to the pre-existing behavior.
"""

from deerflow.config.subagents_config import load_subagents_config_from_dict
from deerflow.subagents import registry as registry_module
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.registry import (
    get_available_subagent_names,
    get_subagent_config,
    get_subagent_names,
)


def _reset():
    """Reset the global subagents config to an empty state."""
    load_subagents_config_from_dict({})


def test_per_user_shadows_global(monkeypatch, tmp_path):
    # global custom via shared storage disabled; emulate global via config.yaml? Use a fresh user file.
    def fake_load_user(name, *, user_id):
        if user_id == "u1" and name == "researcher":
            return SubagentConfig(name="researcher", description="user-desc", system_prompt="user")
        return None

    def fake_list_user_names(*, user_id):
        return ["researcher"] if user_id == "u1" else []

    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    _reset()

    cfg = get_subagent_config("researcher", user_id="u1")
    assert cfg is not None and cfg.description == "user-desc"
    # without user_id: still resolves via global layer (may be None here)
    assert get_subagent_config("researcher") is None


def test_builtin_still_wins_over_user_file(monkeypatch):
    def fake_load_user(name, *, user_id):
        if name == "bash":
            return SubagentConfig(name="bash", description="user-stolen", system_prompt="x")
        return None

    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    # validate path rejects builtin names, but registry hardens too:
    _reset()
    from deerflow.subagents.builtins import BUILTIN_SUBAGENTS

    cfg = get_subagent_config("bash", user_id="u1")
    assert cfg is not None and cfg.description == BUILTIN_SUBAGENTS["bash"].description


def test_get_subagent_names_dedup_with_user_shadow(monkeypatch):
    def fake_list_user_names(*, user_id):
        return ["general-purpose", "researcher"] if user_id == "u1" else []

    def fake_load_user(name, *, user_id):
        if user_id == "u1" and name == "researcher":
            return SubagentConfig(name="researcher", description="u")
        return None

    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    _reset()

    names = get_subagent_names(user_id="u1")
    assert names.count("general-purpose") == 1  # builtin reserved, no dup
    assert "researcher" in names


def test_user_id_none_unchanged(monkeypatch):
    # no user files consulted when user_id is None
    called = {"load": 0, "list": 0}

    def fake_load_user(name, *, user_id):
        called["load"] += 1
        return None

    def fake_list_user_names(*, user_id):
        called["list"] += 1
        return []

    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    _reset()
    get_subagent_config("general-purpose")
    get_subagent_names()
    assert called == {"load": 0, "list": 0}


def test_get_available_subagent_names_forwards_user_id(monkeypatch):
    # get_available_subagent_names must forward user_id to get_subagent_names
    # so per-user names are exposed to the active runtime.
    def fake_list_user_names(*, user_id):
        return ["researcher"] if user_id == "u1" else []

    def fake_load_user(name, *, user_id):
        return None

    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    monkeypatch.setattr(registry_module, "is_host_bash_allowed", lambda *a, **k: True)
    _reset()

    names = get_available_subagent_names(user_id="u1")
    assert "researcher" in names
    assert "general-purpose" in names
    # without user_id the per-user name is absent
    assert "researcher" not in get_available_subagent_names()
