from deerflow.config.subagents_user_config import (
    delete_user_subagent,
    list_user_subagent_names,
    list_user_subagents,
    load_user_subagent,
    save_user_subagent,
    validate_subagent_name,
)
from deerflow.subagents.config import SubagentConfig


def test_validate_subagent_name_rejects_builtins():
    assert validate_subagent_name("general-purpose") is None
    assert validate_subagent_name("bash") is None
    assert validate_subagent_name("") is None
    assert validate_subagent_name("bad name!") is None
    assert validate_subagent_name("deep-researcher") == "deep-researcher"
    assert validate_subagent_name("Deep-Researcher") == "deep-researcher"


def test_save_and_load_user_subagent_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deerflow.config.subagents_user_config.get_paths",
        lambda: type(
            "P",
            (),
            {
                "user_subagents_dir": staticmethod(lambda uid: tmp_path),
                "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml"),
            },
        )(),
    )
    cfg = SubagentConfig(
        name="deep-researcher",
        description="research",
        system_prompt="be terse",
        tools=["read_file"],
        model="inherit",
        max_turns=50,
        timeout_seconds=900,
    )
    save_user_subagent(cfg, "default")

    loaded = load_user_subagent("deep-researcher", "default")
    assert loaded is not None
    assert loaded.name == "deep-researcher"
    assert loaded.system_prompt == "be terse"
    assert loaded.tools == ["read_file"]


def test_save_user_subagent_does_not_persist_disallowed_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deerflow.config.subagents_user_config.get_paths",
        lambda: type(
            "P",
            (),
            {
                "user_subagents_dir": staticmethod(lambda uid: tmp_path),
                "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml"),
            },
        )(),
    )
    save_user_subagent(SubagentConfig(name="x", description="d"), "default")
    text = (tmp_path / "x.yaml").read_text()
    assert "disallowed_tools" not in text


def test_list_user_subagents_skips_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deerflow.config.subagents_user_config.get_paths",
        lambda: type(
            "P",
            (),
            {
                "user_subagents_dir": staticmethod(lambda uid: tmp_path),
                "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml"),
            },
        )(),
    )
    save_user_subagent(SubagentConfig(name="good", description="d"), "default")
    (tmp_path / "bad.yaml").write_text("not: valid: yaml: :\n  :")
    names = list_user_subagent_names("default")
    assert "good" in names
    assert "bad" not in names


def test_list_user_subagents_returns_configs_and_skips_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deerflow.config.subagents_user_config.get_paths",
        lambda: type(
            "P",
            (),
            {
                "user_subagents_dir": staticmethod(lambda uid: tmp_path),
                "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml"),
            },
        )(),
    )
    save_user_subagent(
        SubagentConfig(name="good", description="d", system_prompt="prompt", tools=["read_file"]),
        "default",
    )
    (tmp_path / "bad.yaml").write_text("not: valid: yaml: :\n  :")
    configs = list_user_subagents("default")
    assert [c.name for c in configs] == ["good"]
    good = configs[0]
    assert good.description == "d"
    assert good.system_prompt == "prompt"
    assert good.tools == ["read_file"]
    assert good.disallowed_tools == ["task"]


def test_delete_user_subagent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deerflow.config.subagents_user_config.get_paths",
        lambda: type(
            "P",
            (),
            {
                "user_subagents_dir": staticmethod(lambda uid: tmp_path),
                "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml"),
            },
        )(),
    )
    save_user_subagent(SubagentConfig(name="x", description="d"), "default")
    assert delete_user_subagent("x", "default") is True
    assert load_user_subagent("x", "default") is None
    assert delete_user_subagent("x", "default") is False
